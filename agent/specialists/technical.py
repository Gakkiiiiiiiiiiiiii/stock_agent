from __future__ import annotations

from agent.contracts import AgentRole, AgentTask
from agent.specialists.base import ToolSpecialist


class TechnicalSpecialist(ToolSpecialist):
    role = AgentRole.TECHNICAL
    def __call__(self, task: AgentTask, _shared):
        symbols = list(self.context.get("candidate_symbols") or [])
        if not symbols:
            return self.artifact(task, {}, ["NO_CANDIDATE_SYMBOLS"], 0, unknowns=["TECHNICAL_EVIDENCE_NOT_REQUESTED"])
        technical = [self.call("get_technical_evidence", {"symbol": symbol}) for symbol in symbols]
        refs = [item.get("evidence_id") for item in technical if isinstance(item, dict) and item.get("evidence_id")]
        return self.artifact(task, {"technical_evidence": technical}, tool_calls=len(symbols), evidence_refs=refs)
