"""P2-4 Video Factor V2 测试（§76-77）：KnowledgeUnit 面板、support 门、防前视、V1 回退。"""

from __future__ import annotations

from datetime import datetime

import numpy as np
from sqlalchemy import create_engine

import storage.models.content  # noqa: F401
import storage.models.knowledge  # noqa: F401
import storage.models.vector  # noqa: F401
from engines.factor.video_features import build_video_feature_panel_from_knowledge
from storage.db import Base, SessionLocal
from storage.models.content import VideoAsset
from storage.repositories.knowledge_repository import KnowledgeRepository

SYMBOLS = ["300750.SZ"]
DATES = ["2026-07-09", "2026-07-10", "2026-07-13", "2026-07-14",
         "2026-07-15", "2026-07-16", "2026-07-17"]


def _configure_db(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'factor.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    SessionLocal.configure(bind=engine)
    Base.metadata.create_all(bind=engine)


def _seed_video(suffix: str) -> int:
    with SessionLocal() as session:
        video = VideoAsset(
            platform="bilibili",
            platform_video_id=f"BVFACT{suffix}",
            bvid=f"BVFACT{suffix}",
            url=f"https://example.com/{suffix}",
            title="因子测试",
        )
        session.add(video)
        session.commit()
        return video.id


def _unit(uid: str, **overrides) -> dict:
    unit = {
        "chapter_index": 0,
        "knowledge_uid": uid,
        "primary_domain": "MARKET",
        "knowledge_kind": "FACT",
        "temporal_class": "SNAPSHOT",
        "expression_type": "AUTHOR_EXPLICIT",
        "subject_type": "EQUITY",
        "subject_key": "300750",
        "subject_name": "宁德时代",
        "statement": f"陈述{uid}",
        "canonical_statement": f"陈述{uid}",
        "sentiment": "BULLISH",
        "as_of_time": datetime(2026, 7, 10),
        "lifecycle_status": "ACTIVE",
        "support_status": "SOURCE_SUPPORTED",
        "review_status": "UNREVIEWED",
        "truth_status": "NOT_CHECKED",
        "content_hash": f"hash-{uid}",
        "extractor_version": "test",
        "entities": [{"entity_type": "EQUITY", "entity_key": "300750", "entity_name": "宁德时代", "ticker": "300750", "relation_role": "SUBJECT"}],
    }
    unit.update(overrides)
    return unit


def _seed(repo: KnowledgeRepository, video_suffix: str, units: list[dict]) -> int:
    video_id = _seed_video(video_suffix)
    repo.replace_video_knowledge(
        video_id=video_id,
        chapters=[{
            "chapter_index": 0,
            "start_ms": 0,
            "end_ms": 1000,
            "title": "章节",
            "chapter_type": "ANALYSIS",
            "primary_domain": "MARKET",
            "content_hash": f"chapter-{video_suffix}",
            "parser_version": "test",
        }],
        units=units,
    )
    return video_id


def _seed_main(tmp_path) -> KnowledgeRepository:
    _configure_db(tmp_path)
    repo = KnowledgeRepository()
    _seed(repo, "v1", [
        # u1：看多 FACT 且外部验证 → bullish + verified_catalyst
        _unit("u1", sentiment="BULLISH", knowledge_kind="FACT", truth_status="EXTERNALLY_VERIFIED"),
        # u2：看空 RISK_CONDITION 且外部验证 → bearish + verified_risk（独立 subject，不干扰共识）
        _unit("u2", subject_key="锂电风险", subject_name="锂电风险",
              sentiment="BEARISH", knowledge_kind="RISK_CONDITION", truth_status="EXTERNALLY_VERIFIED"),
        # u3：SOURCE_LOCATED 低于门槛 → 不计入
        _unit("u3", support_status="SOURCE_LOCATED"),
        # u4：人工 REJECTED → 不计入
        _unit("u4", review_status="REJECTED"),
    ])
    _seed(repo, "v2", [
        # u5：另一视频同 subject 看多且外部验证 → 跨视频共识 +1
        _unit("u5", as_of_time=datetime(2026, 7, 14),
              sentiment="BULLISH", knowledge_kind="POLICY_FACT", truth_status="EXTERNALLY_VERIFIED"),
    ])
    return repo


def _build(repo, **kwargs):
    return build_video_feature_panel_from_knowledge(SYMBOLS, DATES, repository=repo, **kwargs)


def test_bullish_bearish_counts_and_support_gate(tmp_path):
    panels, warning = _build(_seed_main(tmp_path))
    assert warning is None
    day = {d: i for i, d in enumerate(DATES)}
    bullish = panels["video_bullish_claim_count"][0]
    bearish = panels["video_bearish_claim_count"][0]
    # 7-13：窗口内仅 u1(BULLISH) + u2(BEARISH)；u3(SOURCE_LOCATED) 与 u4(REJECTED) 被门禁排除
    assert bullish[day["2026-07-13"]] == 1
    assert bearish[day["2026-07-13"]] == 1
    # 7-15：u1 + u5 两个看多
    assert bullish[day["2026-07-15"]] == 2
    assert bearish[day["2026-07-15"]] == 1


def test_verified_catalyst_and_risk_counts(tmp_path):
    panels, _ = _build(_seed_main(tmp_path))
    day = {d: i for i, d in enumerate(DATES)}
    catalyst = panels["verified_catalyst_count"][0]
    risk = panels["verified_risk_count"][0]
    # u1 FACT+BULLISH+EXTERNALLY_VERIFIED → catalyst；u2 RISK_CONDITION+EXTERNALLY_VERIFIED → risk
    assert catalyst[day["2026-07-13"]] == 1
    assert risk[day["2026-07-13"]] == 1
    # 7-15：u5 POLICY_FACT+BULLISH+EXTERNALLY_VERIFIED 加入 → catalyst 2
    assert catalyst[day["2026-07-15"]] == 2
    assert risk[day["2026-07-15"]] == 1


def test_no_lookahead_and_window(tmp_path):
    panels, _ = _build(_seed_main(tmp_path))
    day = {d: i for i, d in enumerate(DATES)}
    bullish = panels["video_bullish_claim_count"][0]
    # as_of 当日（7-10）及之前不可见
    assert bullish[day["2026-07-09"]] == 0
    assert bullish[day["2026-07-10"]] == 0
    # 次一交易日起计入
    assert bullish[day["2026-07-13"]] == 1
    # 7-16：v1 的 u1 已超出 5 天窗口，只剩 u5
    assert bullish[day["2026-07-16"]] == 1
    assert bullish[day["2026-07-17"]] == 1


def test_attention_and_cross_video_consensus(tmp_path):
    panels, _ = _build(_seed_main(tmp_path))
    day = {d: i for i, d in enumerate(DATES)}
    attention = panels["author_attention_score"][0]
    consensus = panels["cross_video_consensus"][0]
    disagreement = panels["cross_video_disagreement"][0]
    # 7-13：2 个 unit / 1 个视频 = 2.0；单视频无跨视频信号
    assert attention[day["2026-07-13"]] == 2.0
    assert consensus[day["2026-07-13"]] == 0
    # 7-15：3 个 unit / 2 个视频 = 1.5；subject 300750 双视频同向看多 → consensus +1
    assert attention[day["2026-07-15"]] == 1.5
    assert consensus[day["2026-07-15"]] == 1
    assert disagreement[day["2026-07-15"]] == 0


def test_cross_video_disagreement(tmp_path):
    _configure_db(tmp_path)
    repo = KnowledgeRepository()
    _seed(repo, "v1", [_unit("ua", sentiment="BULLISH")])
    _seed(repo, "v2", [_unit("ub", sentiment="BEARISH", as_of_time=datetime(2026, 7, 14))])
    panels, _ = _build(repo)
    day = {d: i for i, d in enumerate(DATES)}
    # 7-15：subject 300750 被两个视频覆盖且方向冲突
    assert panels["cross_video_disagreement"][0, day["2026-07-15"]] == 1
    assert panels["cross_video_consensus"][0, day["2026-07-15"]] == 0


def test_empty_db_falls_back_to_v1(tmp_path):
    _configure_db(tmp_path)
    repo = KnowledgeRepository()
    panels, warning = _build(repo, summaries_dir=tmp_path / "no-summaries")
    assert warning and "回退 V1" in warning
    assert set(panels) == {"event_heat", "theme_sentiment"}
    assert np.all(panels["event_heat"] == 0)
