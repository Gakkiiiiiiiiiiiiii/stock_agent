"""视频知识库特征化：把 B 站视频总结事件流转成横截面特征面板。

数据源：knowledge_base/video_summaries/*.md（B 站视频管线产出），解析
「发布时间 / 标的 / 主题」三个字段，输出两个特征面板：
- event_heat：截至当日前 lookback 天内提及该股票（或其所属主题）的视频条数；
- theme_sentiment：同窗口内各视频多空词计数（正向词数 - 负向词数）之和。

时间对齐严禁前视：视频按发布日对齐，发布次日（第一个 > 发布日的交易日）起
才计入窗口。实体→股票映射优先复用 config/entity_aliases.yaml（经
engines/content/financial_entity_normalizer 加载）；主题级提及经
knowledge_base/themes/*.md 的「受益标的」表广播到相关股票。
数据为空时返回全零面板 + warning，不抛异常。

注意：event_heat / theme_sentiment 尚未注册进 engines/factor/vocab.py 词表，
暂不能进 DSL；注册方式建议见主会话规划（在 FEATURES 追加并在 data.py 面板加载时拼接）。
"""
from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

import numpy as np

from engines.content.financial_entity_normalizer import FinancialEntityNormalizer
from financial_agent.utils import project_root

logger = logging.getLogger(__name__)

VIDEO_SUMMARY_DIR = "knowledge_base/video_summaries"
THEMES_DIR = "knowledge_base/themes"

# 内置中文金融多空词表（少量高频词，够用即可）
POSITIVE_WORDS = ("看好", "利好", "超预期", "上涨", "反弹", "景气", "受益", "突破", "增持", "高增长")
NEGATIVE_WORDS = ("看空", "利空", "低于预期", "下跌", "回调", "风险", "警惕", "走弱", "减持", "变脸")

_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_PUB_RE = re.compile(r"发布时间[：:]\s*(\d{4})(\d{2})(\d{2})")
_FILENAME_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})_")
_PAREN_RE = re.compile(r"（.*?）")


def _section_lines(text: str, header: str) -> list[str]:
    """提取 markdown 某二级小节（如「标的」「0. 别名」）下的所有行。"""
    lines: list[str] = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_section = line.lstrip("#").strip().startswith(header)
            continue
        if in_section:
            lines.append(line.strip())
    return lines


def _publish_date(text: str, filename: str) -> date | None:
    """优先取元信息里的发布时间（YYYYMMDD），回退文件名日期前缀。"""
    match = _PUB_RE.search(text)
    if not match:
        match = _FILENAME_DATE_RE.match(filename)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _sentiment(text: str) -> int:
    """简单多空词计数：正向词数 - 负向词数。"""
    pos = sum(text.count(w) for w in POSITIVE_WORDS)
    neg = sum(text.count(w) for w in NEGATIVE_WORDS)
    return pos - neg


def load_theme_map(themes_dir: str | Path | None = None) -> dict[str, list[str]]:
    """主题名/别名 → 受益标的 6 位代码列表（解析 knowledge_base/themes/*.md）。"""
    directory = Path(themes_dir) if themes_dir else project_root() / THEMES_DIR
    theme_map: dict[str, list[str]] = {}
    if not directory.exists():
        return theme_map
    for path in sorted(directory.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取主题文件失败 %s: %s", path, exc)
            continue
        codes = sorted({
            code for line in _section_lines(text, "4. 受益标的")
            for code in _CODE_RE.findall(line)
        })
        if not codes:
            continue
        theme_map[path.stem] = codes
        for alias in _section_lines(text, "0. 别名"):
            name = alias.lstrip("-* ").strip()
            if name:
                theme_map[name] = codes
    return theme_map


def _resolve_stock_codes(lines: list[str], aliases: dict) -> set[str]:
    """「标的」小节条目 → 6 位股票代码（实体别名 EQUITY 映射 + 行内直接出现的代码）。"""
    codes: set[str] = set()
    for line in lines:
        for alias, payload in aliases.items():
            if payload.get("entity_type") != "EQUITY":
                continue
            if alias not in line:
                continue
            match = _CODE_RE.search(str(payload.get("ticker") or ""))
            if match:
                codes.add(match.group(1))
        codes.update(_CODE_RE.findall(_PAREN_RE.sub("", line)))
    return codes


def _resolve_theme_codes(lines: list[str], theme_map: dict) -> set[str]:
    """「主题」小节条目 → 主题受益标的代码（命中不了的主题跳过）。"""
    codes: set[str] = set()
    for line in lines:
        for name, theme_codes in theme_map.items():
            if name in line:
                codes.update(theme_codes)
    return codes


def _parse_summary(path: Path, aliases: dict, theme_map: dict) -> dict | None:
    """解析单个视频总结，返回 {publish, codes, sentiment}；无发布日期则跳过。"""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取视频总结失败 %s: %s", path, exc)
        return None
    publish = _publish_date(text, path.name)
    if publish is None:
        return None
    codes = _resolve_stock_codes(_section_lines(text, "标的"), aliases)
    codes |= _resolve_theme_codes(_section_lines(text, "主题"), theme_map)
    return {"publish": publish, "codes": codes, "sentiment": _sentiment(text)}


def build_video_feature_panel(
    symbols: list[str],
    dates: list[str],
    lookback_days: int = 5,
    summaries_dir: str | Path | None = None,
    aliases_path: str | Path | None = None,
    themes_dir: str | Path | None = None,
) -> tuple[dict[str, np.ndarray], str | None]:
    """构建视频事件特征面板，返回 ({event_heat, theme_sentiment}, warning)。

    event_heat[s, d]：发布日落在 (d-lookback_days, d) 内且提及 s 的视频条数
    （发布当日不计入，次日起计，无前视）；theme_sentiment 同窗口按视频多空计数求和。
    """
    n_symbols, n_days = len(symbols), len(dates)
    panels = {
        "event_heat": np.zeros((n_symbols, n_days)),
        "theme_sentiment": np.zeros((n_symbols, n_days)),
    }
    directory = Path(summaries_dir) if summaries_dir else project_root() / VIDEO_SUMMARY_DIR
    files = sorted(directory.glob("*.md")) if directory.exists() else []
    if not files:
        return panels, "无视频总结数据，返回全零面板"

    aliases = FinancialEntityNormalizer(aliases_path).aliases
    theme_map = load_theme_map(themes_dir)
    videos = [v for v in (_parse_summary(p, aliases, theme_map) for p in files) if v]
    if not videos:
        return panels, "视频总结缺少发布日期，无法对齐，返回全零面板"

    code_index = {}
    for i, symbol in enumerate(symbols):
        match = _CODE_RE.search(str(symbol))
        if match:
            code_index[match.group(1)] = i

    day_dates = [date.fromisoformat(str(d)) for d in dates]
    for video in videos:
        for di, d in enumerate(day_dates):
            delta = (d - video["publish"]).days
            if delta <= 0 or delta > lookback_days:
                continue  # 发布当日及之前不可见；超出回看窗口不再计入
            for code in video["codes"]:
                i = code_index.get(code)
                if i is None:
                    continue
                panels["event_heat"][i, di] += 1
                panels["theme_sentiment"][i, di] += video["sentiment"]
    return panels, None


__all__ = ["build_video_feature_panel", "load_theme_map", "POSITIVE_WORDS", "NEGATIVE_WORDS"]
