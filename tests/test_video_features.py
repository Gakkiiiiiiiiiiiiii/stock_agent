"""视频知识库特征化测试：时间对齐（无前视）、热度计数、情感计数、空数据降级。"""
import numpy as np

from engines.factor.video_features import build_video_feature_panel, load_theme_map

SYMBOLS = ["300750.SZ", "600900.SH", "601088.SH", "000001.SZ"]
DATES = ["2026-07-09", "2026-07-10", "2026-07-13", "2026-07-14",
         "2026-07-15", "2026-07-16", "2026-07-17"]


def _write(path, text):
    path.write_text(text, encoding="utf-8")


def _env(tmp_path):
    """模拟别名库 / 主题库 / 两个视频总结。"""
    aliases = tmp_path / "aliases.yaml"
    _write(aliases, (
        "aliases:\n"
        "  宁德时代:\n"
        "    entity_type: EQUITY\n"
        '    ticker: "300750"\n'
        "  长江电力:\n"
        "    entity_type: EQUITY\n"
        '    ticker: "600900"\n'
    ))
    themes = tmp_path / "themes"
    themes.mkdir()
    _write(themes / "测试主题.md", (
        "# 测试主题\n\n"
        "## 0. 别名\n- 测试别名\n\n"
        "## 4. 受益标的\n"
        "| 标的 | 名称 |\n|---|---|\n| 600900 | 长江电力 |\n| 601088 | 中国神华 |\n"
    ))
    summaries = tmp_path / "summaries"
    summaries.mkdir()
    # 视频 1：7-10 发布，提及宁德时代 + 测试主题，3 个正向词
    _write(summaries / "20260710_BV001_测试.md", (
        "# 视频一\n\n"
        "## 元信息\n- 发布时间：20260710\n\n"
        "## 核心摘要\n看好景气方向，利好明显，业绩超预期。\n\n"
        "## 主题\n- 测试主题\n\n"
        "## 标的\n- 宁德时代\n"
    ))
    # 视频 2：7-14 发布，提及长江电力，1 个正向词 2 个负向词 → 情感 -1
    _write(summaries / "20260714_BV002_测试.md", (
        "# 视频二\n\n"
        "## 元信息\n- 发布时间：20260714\n\n"
        "## 核心摘要\n短期利好，但要警惕回调风险。\n\n"
        "## 标的\n- 长江电力\n"
    ))
    return {"aliases": aliases, "themes": themes, "summaries": summaries}


def _build(env, lookback_days=5):
    return build_video_feature_panel(
        SYMBOLS, DATES, lookback_days=lookback_days,
        summaries_dir=env["summaries"], aliases_path=env["aliases"], themes_dir=env["themes"],
    )


def test_time_alignment_no_lookahead(tmp_path):
    """发布当日不计入，次日（第一个 > 发布日的交易日）起才可见。"""
    panels, warning = _build(_env(tmp_path))
    assert warning is None
    heat = panels["event_heat"]
    idx = {s: i for i, s in enumerate(SYMBOLS)}
    # 视频 1 发布日 7-10（含）之前全为 0
    assert heat[idx["300750.SZ"], DATES.index("2026-07-10")] == 0
    # 次一交易日 7-13 起计入
    assert heat[idx["300750.SZ"], DATES.index("2026-07-13")] == 1


def test_event_heat_count_and_window(tmp_path):
    panels, _ = _build(_env(tmp_path))
    heat = panels["event_heat"]
    idx = {s: i for i, s in enumerate(SYMBOLS)}
    # 7-15：视频 1（主题广播）与视频 2（直接提及）都覆盖 600900 → 热度 2
    assert heat[idx["600900.SH"], DATES.index("2026-07-15")] == 2
    # 主题广播到 601088（仅视频 1 提及主题）
    assert heat[idx["601088.SH"], DATES.index("2026-07-13")] == 1
    # 7-16：视频 1 已超出 5 天窗口（7-10 发布），只剩视频 2 → 热度 1
    assert heat[idx["600900.SH"], DATES.index("2026-07-16")] == 1
    # 未被任何视频提及的标的恒为 0
    assert heat[idx["000001.SZ"]].sum() == 0


def test_theme_sentiment_count(tmp_path):
    panels, _ = _build(_env(tmp_path))
    senti = panels["theme_sentiment"]
    idx = {s: i for i, s in enumerate(SYMBOLS)}
    # 视频 1：看好/景气/利好/超预期 = +4，7-13 计入 300750 与主题股 600900
    assert senti[idx["300750.SZ"], DATES.index("2026-07-13")] == 4
    assert senti[idx["600900.SH"], DATES.index("2026-07-13")] == 4
    # 7-15：视频 1(+4) + 视频 2(利好-警惕-回调-风险 = 1-3 = -2) → 2
    assert senti[idx["600900.SH"], DATES.index("2026-07-15")] == 2
    # 7-16 起只剩视频 2 → -2
    assert senti[idx["600900.SH"], DATES.index("2026-07-16")] == -2


def test_theme_alias_broadcast(tmp_path):
    """主题以别名形式提及也能广播到受益标的。"""
    env = _env(tmp_path)
    _write(env["summaries"] / "20260710_BV003_别名.md", (
        "# 视频三\n\n## 元信息\n- 发布时间：20260710\n\n"
        "## 核心摘要\n看好。\n\n## 主题\n- 测试别名\n\n## 标的\n"
    ))
    panels, _ = _build(env)
    idx = {s: i for i, s in enumerate(SYMBOLS)}
    assert panels["event_heat"][idx["601088.SH"], DATES.index("2026-07-13")] == 2


def test_empty_summaries_degrades(tmp_path):
    env = _env(tmp_path)
    panels, warning = build_video_feature_panel(
        SYMBOLS, DATES, summaries_dir=tmp_path / "empty",
        aliases_path=env["aliases"], themes_dir=env["themes"],
    )
    assert warning
    assert panels["event_heat"].shape == (4, 7)
    assert np.all(panels["event_heat"] == 0)
    assert np.all(panels["theme_sentiment"] == 0)


def test_load_theme_map(tmp_path):
    env = _env(tmp_path)
    theme_map = load_theme_map(env["themes"])
    assert theme_map["测试主题"] == ["600900", "601088"]
    assert theme_map["测试别名"] == ["600900", "601088"]
