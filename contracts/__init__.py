"""Versioned cross-service DTOs; services never exchange Python internals."""

from contracts.evidence import DependencyStatus, Evidence, EvidenceQuality, EvidenceType, SourceSystem
from contracts.decision import FinalInvestmentDecision, PolicyCheckResult, PolicyEvaluation
from contracts.proposal import (
    DecisionHorizon, InvestmentProposalV2, ModelIdentity, NarrativeReport, ThesisPoint,
    adapt_v1_to_v2, adapt_v2_to_v1, proposal_v1_to_v2, proposal_v2_to_v1,
)

__all__ = [
    "DependencyStatus", "Evidence", "EvidenceQuality", "EvidenceType", "SourceSystem",
    "DecisionHorizon", "ThesisPoint", "ModelIdentity", "NarrativeReport", "InvestmentProposalV2",
    "PolicyCheckResult", "PolicyEvaluation", "FinalInvestmentDecision",
    "proposal_v1_to_v2", "proposal_v2_to_v1", "adapt_v1_to_v2", "adapt_v2_to_v1",
]
