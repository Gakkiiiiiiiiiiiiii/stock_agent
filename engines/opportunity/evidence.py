"""证据引用组装：candidate evidence_ids + 成分 provenance，稳定且确定。"""
from __future__ import annotations

from collections.abc import Iterable

from engines.opportunity.candidate import COMPONENT_ORDER, OpportunityCandidate


def build_evidence_refs(candidate: OpportunityCandidate, present_components: Iterable[str] = ()) -> list[str]:
    """生成稳定 evidence_refs。

    顺序固定：先按 COMPONENT_ORDER 排列的成分 provenance（component:<name>），
    再按候选原始顺序追加 evidence:<id>。相同输入产生相同列表。
    """
    present = set(present_components)
    refs = [f"component:{name}" for name in COMPONENT_ORDER if name in present]
    refs.extend(f"evidence:{evidence_id}" for evidence_id in candidate.evidence_ids)
    return refs
