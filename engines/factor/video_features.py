"""视频知识库特征化：把 B 站视频知识流转成横截面特征面板。

两条数据路径：

- V1（fallback）：knowledge_base/video_summaries/*.md，解析「发布时间 / 标的 / 主题」，
  用多空词计数做 sentiment，输出 {event_heat, theme_sentiment} 面板。
- V2（P2-4，设计文档 §76-77）：build_video_feature_panel_from_knowledge 直接查询
  versioned KnowledgeUnit（经 KnowledgeRepository.list_units_for_factor），只消费
  support_rank >= SOURCE_SUPPORTED 且 review_status != REJECTED 的 unit，按 unit 的
  sentiment / truth_status / knowledge_kind 字段统计，不再做文本词频。输出
  V2_FEATURE_NAMES 列出的 7 个面板；无 DB 数据时回退 V1 路径并给出 warning。

时间对齐严禁前视：unit 按 as_of_time（视频发布日口径）对齐，次日（第一个 > as_of
日的交易日）起才计入窗口，与 V1 规则一致。实体→股票映射复用 unit 实体表中的 6 位
ticker 与 subject_key 中的 6 位代码。数据为空时返回全零面板 + warning，不抛异常。

注意：event_heat / theme_sentiment 及 V2 的 7 个特征均尚未注册进
engines/factor/vocab.py 词表，暂不能进 DSL；注册方式建议见主会话规划（在
FEATURES 追加并在 data.py 面板加载时拼接）。
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


# ---------------------------------------------------------------------------
# V2：直接消费 KnowledgeUnit（P2-4，设计文档 §77）
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta  # noqa: E402

from engines.content.knowledge_enums import support_rank  # noqa: E402

V2_FEATURE_NAMES = (
    "video_bullish_claim_count",
    "video_bearish_claim_count",
    "verified_catalyst_count",
    "verified_risk_count",
    "author_attention_score",
    "cross_video_consensus",
    "cross_video_disagreement",
)

_BULLISH = "BULLISH"
_BEARISH = "BEARISH"
_VERIFIED = "EXTERNALLY_VERIFIED"
# §77「利好类」kind：事实 / 政策 / 财务指标 / 因果论点的看多表达视为催化剂。
_CATALYST_KINDS = frozenset({"FACT", "POLICY_FACT", "FINANCIAL_METRIC", "CAUSAL_THESIS"})
_RISK_KINDS = frozenset({"RISK_CONDITION"})


def _unit_effective_date(unit: dict) -> date | None:
    """unit 的对齐日期（as_of_time 的日历日）；无法解析返回 None（不参与面板）。"""
    raw = unit.get("as_of_time")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)).date()
    except ValueError:
        return None


def _unit_codes(unit: dict) -> set[str]:
    """unit → 6 位股票代码：实体表 ticker + subject_key 中直接出现的代码。"""
    codes: set[str] = set()
    for entity in unit.get("entities") or []:
        match = _CODE_RE.search(str(entity.get("ticker") or ""))
        if match:
            codes.add(match.group(1))
    match = _CODE_RE.search(str(unit.get("subject_key") or ""))
    if match:
        codes.add(match.group(1))
    return codes


def _unit_sentiment(unit: dict) -> str:
    return str(unit.get("sentiment") or "").strip().upper()


def build_video_feature_panel_from_knowledge(
    symbols: list[str],
    dates: list[str],
    lookback_days: int = 5,
    *,
    repository=None,
    summaries_dir: str | Path | None = None,
    aliases_path: str | Path | None = None,
    themes_dir: str | Path | None = None,
) -> tuple[dict[str, np.ndarray], str | None]:
    """V2 视频因子面板：直接消费 KnowledgeUnit，返回 ({特征: 面板}, warning)。

    面板结构为 symbols × dates，与 V1 兼容。特征（§77）：

    - video_bullish_claim_count / video_bearish_claim_count：窗口内该标的
      sentiment 为 BULLISH / BEARISH 的 unit 数（字段统计，非词频）；
    - verified_catalyst_count：truth_status==EXTERNALLY_VERIFIED 且 sentiment
      为 BULLISH 且 kind 属利好类（FACT/POLICY_FACT/FINANCIAL_METRIC/CAUSAL_THESIS）；
    - verified_risk_count：EXTERNALLY_VERIFIED 且（kind==RISK_CONDITION 或 sentiment
      为 BEARISH）；
    - author_attention_score：窗口内该标的 unit 数 / 涉及视频数（每视频 claim 密度）；
    - cross_video_consensus：同 subject 被 >=2 个视频覆盖且方向一致（全多 +1 /
      全空 -1，按 subject 累加）；
    - cross_video_disagreement：同 subject 被 >=2 个视频覆盖且方向冲突的 subject 数。

    防前视与 V1 一致：unit 按 as_of_time 对齐，次日起计，窗口 lookback_days 天。
    仅消费 support_rank >= SOURCE_SUPPORTED 且 review_status != REJECTED 的 unit
    （由 KnowledgeRepository.list_units_for_factor 保证）。DB 查询失败或无数据时
    回退 V1 Markdown 路径并返回 warning。
    """
    day_dates = [date.fromisoformat(str(d)) for d in dates]
    units: list[dict] = []
    db_warning: str | None = None
    try:
        repo = repository
        if repo is None:
            from storage.repositories.knowledge_repository import KnowledgeRepository

            repo = KnowledgeRepository()
        start = datetime.combine(min(day_dates) - timedelta(days=lookback_days + 1), datetime.min.time())
        end = datetime.combine(max(day_dates) + timedelta(days=1), datetime.min.time())
        units = repo.list_units_for_factor(start, end, minimum_support_status="SOURCE_SUPPORTED")
    except Exception as exc:  # noqa: BLE001
        db_warning = f"KnowledgeUnit 查询失败（{exc}），回退 V1 Markdown 面板"
        logger.warning(db_warning)

    if not units:
        panels, warning = build_video_feature_panel(
            symbols, dates, lookback_days=lookback_days,
            summaries_dir=summaries_dir, aliases_path=aliases_path, themes_dir=themes_dir,
        )
        prefix = db_warning or "KnowledgeUnit 无可用数据，回退 V1 Markdown 面板"
        logger.warning("%s（%s）", prefix, warning)
        return panels, f"{prefix}；{warning}" if warning else prefix

    n_symbols, n_days = len(symbols), len(dates)
    panels = {name: np.zeros((n_symbols, n_days)) for name in V2_FEATURE_NAMES}

    code_index = {}
    for i, symbol in enumerate(symbols):
        match = _CODE_RE.search(str(symbol))
        if match:
            code_index[match.group(1)] = i

    # 每个 (symbol, day) 单元格收集窗口内 unit，再统一统计，保证口径一致。
    cells: dict[tuple[int, int], list[dict]] = {}
    for unit in units:
        effective = _unit_effective_date(unit)
        if effective is None:
            continue
        row_indexes = [code_index[code] for code in _unit_codes(unit) if code in code_index]
        if not row_indexes:
            continue
        for di, d in enumerate(day_dates):
            delta = (d - effective).days
            if delta <= 0 or delta > lookback_days:
                continue  # as_of 当日及之前不可见；超出回看窗口不再计入
            for i in row_indexes:
                cells.setdefault((i, di), []).append(unit)

    for (i, di), cell_units in cells.items():
        bullish = bearish = catalyst = risk = 0
        videos: set = set()
        # subject_key -> {video_id: set(sentiment)}
        subject_video_sentiments: dict[str, dict] = {}
        for unit in cell_units:
            sentiment = _unit_sentiment(unit)
            kind = str(unit.get("knowledge_kind") or "").upper()
            verified = str(unit.get("truth_status") or "").upper() == _VERIFIED
            video_id = unit.get("source_video_id")
            videos.add(video_id)
            if sentiment == _BULLISH:
                bullish += 1
            elif sentiment == _BEARISH:
                bearish += 1
            if verified and sentiment == _BULLISH and kind in _CATALYST_KINDS:
                catalyst += 1
            if verified and (kind in _RISK_KINDS or sentiment == _BEARISH):
                risk += 1
            subject = str(unit.get("subject_key") or "")
            if subject and sentiment in {_BULLISH, _BEARISH}:
                per_video = subject_video_sentiments.setdefault(subject, {})
                per_video.setdefault(video_id, set()).add(sentiment)
        panels["video_bullish_claim_count"][i, di] = bullish
        panels["video_bearish_claim_count"][i, di] = bearish
        panels["verified_catalyst_count"][i, di] = catalyst
        panels["verified_risk_count"][i, di] = risk
        panels["author_attention_score"][i, di] = len(cell_units) / max(len(videos), 1)
        consensus = disagreement = 0
        for per_video in subject_video_sentiments.values():
            if len(per_video) < 2:
                continue  # 单视频不构成跨视频信号
            directions = set().union(*per_video.values())
            if directions == {_BULLISH}:
                consensus += 1
            elif directions == {_BEARISH}:
                consensus -= 1
            else:
                disagreement += 1
        panels["cross_video_consensus"][i, di] = consensus
        panels["cross_video_disagreement"][i, di] = disagreement
    return panels, None


__all__ = [
    "build_video_feature_panel",
    "build_video_feature_panel_from_knowledge",
    "load_theme_map",
    "POSITIVE_WORDS",
    "NEGATIVE_WORDS",
    "V2_FEATURE_NAMES",
]
