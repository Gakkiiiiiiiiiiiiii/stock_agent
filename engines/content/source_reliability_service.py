"""P1-5（设计文档 §56-57）：来源/作者可靠性计算服务。

按 作者 / 来源平台 维度，从历史 KnowledgeUnit 计算可靠性指标：

- factual_accuracy：该来源历史 unit 中 truth_status == EXTERNALLY_VERIFIED 占
  「eligible 外部验证 unit」（即真正跑过外部验证：external_verification_status 为
  EXTERNAL_MATCH / EXTERNAL_CONFLICT / EXTERNAL_NOT_FOUND）的比例；无 eligible
  样本时为 None；
- sample_size：eligible 外部验证 unit 数；
- forecast_score：FORECAST 类 unit 中被后续关系印证（作为 SUPERSEDES / VALIDATES
  关系的 target，或 lifecycle_status == VALIDATED）的比例；FORECAST 样本不足
  min_forecast_sample 时为 None。

写回值 reliability_score = factual_accuracy，缺失时退化为 forecast_score。

重要边界（§57）：

- 作者可靠性不能替代单条 Evidence Verification，只能作为检索排序的弱信号；
- 视频表（video_asset）带 author_id / author_name 字段时按作者维度聚合；
  缺失作者信息的视频退化为 ``video:{source_video_id}`` 维度；
- 本服务不接入主链路自动运行（避免与 ingest / retrieval 循环依赖），由离线
  script（scripts/backfill_source_reliability.py）或手动调用触发。
"""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import or_, select

from storage.db import session_scope
from storage.models.content import VideoAsset
from storage.models.knowledge import KnowledgeUnit, KnowledgeUnitRelation

logger = logging.getLogger(__name__)

# 真正跑过外部验证（eligible）的 external_verification_status 取值。
_ELIGIBLE_EXTERNAL_STATUSES = frozenset({"EXTERNAL_MATCH", "EXTERNAL_CONFLICT", "EXTERNAL_NOT_FOUND"})
# FORECAST 被后续印证的关系类型（unit 作为 target）。
_FORECAST_CONFIRM_RELATIONS = ("SUPERSEDES", "VALIDATES")
_FORECAST_KIND = "FORECAST"
_VALIDATED_LIFECYCLE = "VALIDATED"


def _author_key(video: VideoAsset) -> str:
    """作者维度聚合 key：优先 author_id，退化为 author_name，再退化为视频维度。"""
    if video.author_id:
        return str(video.author_id)
    if video.author_name:
        return f"name:{video.author_name}"
    return f"video:{video.id}"


class SourceReliabilityService:
    """作者/来源可靠性计算与回填（离线服务，不进主链路）。"""

    def __init__(self, *, min_forecast_sample: int = 5) -> None:
        self.min_forecast_sample = int(min_forecast_sample)

    def compute(self, source_key: str) -> dict:
        """计算单个来源的可靠性。source_key 匹配 author_id 或 author_name。

        返回 {"source_type", "source_key", "author_id", "factual_accuracy",
        "sample_size", "forecast_score", "reliability_score", "video_count"}。
        """
        with session_scope() as session:
            rows = self._load_units(session, source_key)
            return self._compute_stats(session, source_key, rows)

    def backfill(self, repository=None) -> dict:
        """按作者维度批量计算并写回 knowledge_unit.source_reliability_score。

        repository 参数仅为签名兼容/未来扩展保留；当前实现直接用 session 批量更新。
        返回 {"sources": {source_key: stats}, "units_updated": int}。
        """
        del repository  # 见 docstring
        with session_scope() as session:
            rows = self._load_units(session, None)
            by_source: dict[str, list[tuple[KnowledgeUnit, VideoAsset]]] = defaultdict(list)
            for unit, video in rows:
                by_source[_author_key(video)].append((unit, video))
            sources: dict[str, dict] = {}
            units_updated = 0
            for source_key, source_rows in sorted(by_source.items()):
                stats = self._compute_stats(session, source_key, source_rows)
                sources[source_key] = stats
                score = stats["reliability_score"]
                if score is None:
                    continue
                for unit, _video in source_rows:
                    unit.source_reliability_score = score
                    session.add(unit)
                    units_updated += 1
            logger.info("source reliability backfill: %d sources, %d units updated", len(sources), units_updated)
            return {"sources": sources, "units_updated": units_updated}

    # ------------------------------------------------------------------

    @staticmethod
    def _load_units(session, source_key: str | None) -> list[tuple[KnowledgeUnit, VideoAsset]]:
        statement = select(KnowledgeUnit, VideoAsset).join(
            VideoAsset, KnowledgeUnit.source_video_id == VideoAsset.id
        )
        if source_key:
            statement = statement.where(
                or_(VideoAsset.author_id == source_key, VideoAsset.author_name == source_key)
            )
        return list(session.execute(statement).all())

    def _compute_stats(
        self,
        session,
        source_key: str,
        rows: list[tuple[KnowledgeUnit, VideoAsset]],
    ) -> dict:
        eligible = [
            unit for unit, _ in rows
            if str(unit.external_verification_status or "").upper() in _ELIGIBLE_EXTERNAL_STATUSES
        ]
        verified = [
            unit for unit in eligible
            if str(unit.truth_status or "").upper() == "EXTERNALLY_VERIFIED"
        ]
        sample_size = len(eligible)
        factual_accuracy = (len(verified) / sample_size) if sample_size else None

        forecasts = [
            unit for unit, _ in rows
            if str(unit.knowledge_kind or "").upper() == _FORECAST_KIND
        ]
        forecast_score = None
        if len(forecasts) >= self.min_forecast_sample:
            confirmed_ids = self._confirmed_forecast_ids(session, [unit.id for unit in forecasts])
            confirmed = sum(
                1
                for unit in forecasts
                if unit.id in confirmed_ids or str(unit.lifecycle_status or "").upper() == _VALIDATED_LIFECYCLE
            )
            forecast_score = confirmed / len(forecasts)

        reliability_score = factual_accuracy if factual_accuracy is not None else forecast_score
        author_ids = sorted({str(video.author_id) for _, video in rows if video.author_id})
        return {
            "source_type": "video_creator",
            "source_key": source_key,
            "author_id": author_ids[0] if len(author_ids) == 1 else None,
            "factual_accuracy": factual_accuracy,
            "sample_size": sample_size,
            "forecast_score": forecast_score,
            "reliability_score": reliability_score,
            "video_count": len({video.id for _, video in rows}),
            "unit_count": len(rows),
        }

    @staticmethod
    def _confirmed_forecast_ids(session, forecast_ids: list[int]) -> set[int]:
        """被后续 SUPERSEDES / VALIDATES 关系印证（作为 target）的 forecast unit id。"""
        if not forecast_ids:
            return set()
        rows = session.execute(
            select(KnowledgeUnitRelation.target_unit_id).where(
                KnowledgeUnitRelation.target_unit_id.in_(forecast_ids),
                KnowledgeUnitRelation.relation_type.in_(_FORECAST_CONFIRM_RELATIONS),
            )
        ).all()
        return {row[0] for row in rows}


__all__ = ["SourceReliabilityService"]
