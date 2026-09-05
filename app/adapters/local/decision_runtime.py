"""Local composition adapters for the decision façade.

Concrete persistence and model integrations live here so the façade itself
only coordinates ports and phase collaborators.
"""
from engines.advisory.models import InvestorProfile, Recommendation
from engines.advisory.suitability import evaluate_suitability
from engines.decision.conflict_resolver import resolve_conflicts_v2
from engines.decision.decision_service import DecisionService
from engines.decision.outcome_service import OutcomeService
from engines.decision.review_service import ReviewService
from engines.decision.runtime_mode import RuntimeMode, build_runtime_segment
from engines.policy.engine import PolicyEngine
from engines.policy.models import InvestmentProposal, PolicyContext
from storage.repositories.decision_input_repository import DecisionInputBundleRepository
from storage.repositories.research_repository import DecisionSnapshotRepository
from storage.repositories.tool_result_repository import ToolResultRepository

__all__ = [
    "DecisionInputBundleRepository",
    "DecisionService",
    "DecisionSnapshotRepository",
    "InvestmentProposal",
    "InvestorProfile",
    "OutcomeService",
    "PolicyContext",
    "PolicyEngine",
    "Recommendation",
    "ReviewService",
    "RuntimeMode",
    "ToolResultRepository",
    "build_runtime_segment",
    "evaluate_suitability",
    "resolve_conflicts_v2",
]
