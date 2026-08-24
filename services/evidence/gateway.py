from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from contracts.evidence import DependencyStatus, DependencyStatusValue, Evidence, EvidenceQuality, EvidenceType, SourceSystem
from clients._http import CONTRACT_MISMATCH, DEPENDENCY_TIMEOUT, DEPENDENCY_UNAVAILABLE, INVALID_SNAPSHOT, STALE_DATA, DependencyError
from app.model_gateway.metrics import MetricsRecorder, TraceContext, global_metrics
from services.evidence.content_adapter import ContentEvidenceAdapter
from services.evidence.factor_adapter import FactorEvidenceAdapter
from services.evidence.market_adapter import MarketEvidenceAdapter
from services.evidence.portfolio_adapter import PortfolioEvidenceAdapter
from services.evidence.validators import freshness_quality, validate_available_at, validate_evidence

_SNAPSHOT_SELECTORS = frozenset({"latest", "current", "default"})


class GatewayRequestError(ValueError):
    """Invalid caller operation; do not convert to dependency health."""


class ExternalContractError(ValueError):
    """External response violates an evidence contract."""


class ExternalStaleError(ExternalContractError):
    """External response is stale or beyond the point-in-time cutoff."""


class EvidenceGateway:
    """Fetch, validate and deduplicate external facts without making decisions."""

    def __init__(self, *, quant_client=None, factor_client=None, content_client=None, clock=None, metrics: MetricsRecorder | None = None) -> None:
        self.quant_client = quant_client
        self.factor_client = factor_client
        self.content_client = content_client
        self.clock = clock
        self.metrics = metrics or global_metrics()
        self.market_adapter = MarketEvidenceAdapter()
        self.factor_adapter = FactorEvidenceAdapter()
        self.content_adapter = ContentEvidenceAdapter()
        self.portfolio_adapter = PortfolioEvidenceAdapter()

    def collect(self, requests: list[dict[str, Any]], *, decision_time: datetime, trusted_payload: bool = False, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> tuple[list[Evidence], list[DependencyStatus]]:
        if decision_time.tzinfo is None or decision_time.utcoffset() is None:
            raise ValueError("decision_time must be timezone-aware")
        evidence: list[Evidence] = []
        statuses: list[DependencyStatus] = []
        for request in requests:
            try:
                item = self._collect_one(request, decision_time=decision_time, trusted_payload=trusted_payload, trace=trace, trace_context=trace_context)
                try:
                    validate_available_at(item, decision_time)
                except ValueError as exc:
                    raise ExternalStaleError(str(exc)) from exc
                evidence.append(item)
                item_status = DependencyStatusValue.STALE if item.quality_status == EvidenceQuality.STALE else DependencyStatusValue.DEGRADED if item.quality_status in {EvidenceQuality.PARTIAL, EvidenceQuality.DEGRADED, EvidenceQuality.UNVERIFIED} else DependencyStatusValue.OK
                statuses.append(self._status(item.source_system, item_status, contract_version=item.contract_version, service_version=request.get("service_version") or item.data_version, snapshot_id=item.snapshot_id, reason_codes=["STALE_DATA"] if item_status == DependencyStatusValue.STALE else []))
            except GatewayRequestError:
                raise
            except ExternalStaleError as exc:
                system = self._request_system(request)
                self.metrics.increment("dependency_degraded_total", dependency=system.value, code=STALE_DATA)
                statuses.append(self._status(system, DependencyStatusValue.STALE, contract_version=request.get("contract_version"), service_version=request.get("service_version"), snapshot_id=self._selector_snapshot_id(request), reason_codes=["STALE_DATA", type(exc).__name__]))
            except ExternalContractError as exc:
                system = self._request_system(request)
                reason = self._reason_code(exc)
                self.metrics.increment("dependency_degraded_total", dependency=system.value, code=reason)
                statuses.append(self._status(system, DependencyStatusValue.DEGRADED, contract_version=request.get("contract_version"), service_version=request.get("service_version"), snapshot_id=self._selector_snapshot_id(request), reason_codes=[reason]))
            except DependencyError as exc:
                system = self._request_system(request)
                status, reason = self._dependency_failure(exc.code)
                self.metrics.increment("dependency_degraded_total", dependency=system.value, code=reason)
                statuses.append(self._status(system, status, contract_version=request.get("contract_version"), service_version=request.get("service_version"), snapshot_id=self._selector_snapshot_id(request), reason_codes=[reason]))
            except (ValueError, KeyError):
                raise
            except Exception as exc:  # noqa: BLE001
                system = self._request_system(request)
                reason = self._reason_code(exc)
                self.metrics.increment("dependency_degraded_total", dependency=system.value, code=reason)
                statuses.append(self._status(system, self._failure_status(exc), reason_codes=[reason]))
        return self.deduplicate(evidence), self._consolidate_statuses(statuses)

    @staticmethod
    def deduplicate(evidence: list[Evidence]) -> list[Evidence]:
        unique: dict[str, Evidence] = {}
        for item in evidence:
            old = unique.get(item.evidence_id or "")
            if old is not None and old.model_dump(mode="json") != item.model_dump(mode="json"):
                raise ValueError(f"conflicting duplicate evidence id: {item.evidence_id}")
            unique[item.evidence_id or ""] = item
        return list(unique.values())

    def _collect_one(self, request: dict[str, Any], *, decision_time: datetime, trusted_payload: bool = False, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> Evidence:
        system = self._request_system(request)
        if system == SourceSystem.STOCK_AGENT:
            raise GatewayRequestError("external gateway cannot fetch stock_agent-owned memory")
        if request.get("method") or request.get("path"):
            raise GatewayRequestError("gateway does not accept arbitrary method/path fields")
        payload = request.get("payload")
        if payload is not None and not trusted_payload:
            raise GatewayRequestError("payload injection is restricted to trusted offline collection")
        if payload is None:
            payload = self._dispatch(system, request, trace=trace, trace_context=trace_context)
        if not isinstance(payload, dict):
            raise ExternalContractError("evidence client payload must be an object")
        try:
            evidence_type = EvidenceType(request.get("evidence_type", _default_type(system)))
        except ValueError as exc:
            raise GatewayRequestError("unsupported evidence type") from exc
        allowed_types = {
            SourceSystem.QUANT: {EvidenceType.MARKET_SNAPSHOT, EvidenceType.MARKET_REGIME, EvidenceType.MARKET_BREADTH, EvidenceType.SECTOR_STRENGTH, EvidenceType.TECHNICAL_SIGNAL, EvidenceType.TECHNICAL_PROFILE, EvidenceType.LIQUIDITY, EvidenceType.PORTFOLIO_POSITION, EvidenceType.PORTFOLIO_EXPOSURE, EvidenceType.PORTFOLIO_RISK, EvidenceType.BACKTEST_RESULT},
            SourceSystem.FACTOR: {EvidenceType.FACTOR_SCORE, EvidenceType.FACTOR_SET, EvidenceType.FACTOR_RESEARCH_RESULT},
            SourceSystem.CONTENT: {EvidenceType.KNOWLEDGE_CLAIM, EvidenceType.CATALYST, EvidenceType.RISK_EVENT, EvidenceType.VALUATION_FACT, EvidenceType.EARNINGS_FACT},
        }
        if evidence_type not in allowed_types[system]:
            raise GatewayRequestError(f"evidence type {evidence_type.value} is incompatible with {system.value}")
        operation_types = {
            "market_snapshot": {EvidenceType.MARKET_SNAPSHOT},
            "market_breadth": {EvidenceType.MARKET_BREADTH},
            "market_regime": {EvidenceType.MARKET_REGIME},
            "sector_strength": {EvidenceType.SECTOR_STRENGTH},
            "technical_evidence": {EvidenceType.TECHNICAL_SIGNAL, EvidenceType.TECHNICAL_PROFILE, EvidenceType.LIQUIDITY},
            "portfolio_snapshot": {EvidenceType.PORTFOLIO_POSITION, EvidenceType.PORTFOLIO_EXPOSURE},
            "portfolio_risk": {EvidenceType.PORTFOLIO_RISK},
            "backtest": {EvidenceType.BACKTEST_RESULT},
            "factor_score": {EvidenceType.FACTOR_SCORE},
            "factor_set": {EvidenceType.FACTOR_SET},
            "factor_evidence": {EvidenceType.FACTOR_SCORE, EvidenceType.FACTOR_SET, EvidenceType.FACTOR_RESEARCH_RESULT},
            "factor_research": {EvidenceType.FACTOR_RESEARCH_RESULT},
            "content_search": {EvidenceType.KNOWLEDGE_CLAIM, EvidenceType.CATALYST, EvidenceType.RISK_EVENT},
            "knowledge_unit": {EvidenceType.KNOWLEDGE_CLAIM, EvidenceType.CATALYST, EvidenceType.RISK_EVENT, EvidenceType.VALUATION_FACT, EvidenceType.EARNINGS_FACT},
            "content_catalyst": {EvidenceType.CATALYST},
        }
        operation = request.get("operation")
        if operation in operation_types and evidence_type not in operation_types[operation]:
            raise GatewayRequestError(f"evidence type {evidence_type.value} is incompatible with operation {operation}")
        as_of_raw = request.get("as_of") or payload.get("as_of") or payload.get("data_as_of")
        if as_of_raw is None:
            raise ExternalContractError("external evidence must provide as_of")
        try:
            as_of = _timestamp(as_of_raw, decision_time)
        except ValueError as exc:
            raise ExternalContractError(str(exc)) from exc
        if as_of > decision_time:
            raise ExternalStaleError("look-ahead as_of evidence")
        available_raw = request.get("available_at") or payload.get("available_at") or payload.get("availableAt")
        if available_raw is None:
            raise ExternalContractError("external evidence must provide available_at")
        try:
            available_at = _timestamp(available_raw, decision_time)
        except ValueError as exc:
            raise ExternalContractError(str(exc)) from exc
        contract_version = request.get("contract_version") or payload.get("contract_version")
        if contract_version is None:
            contract_version = _default_contract(system)
        if contract_version != _default_contract(system):
            raise ExternalContractError(f"unsupported evidence contract version: {contract_version}")
        quality = request.get("quality_status") or payload.get("quality_status") or payload.get("quality") or EvidenceQuality.PARTIAL
        provider_snapshot_id = payload.get("snapshot_id") or payload.get("snapshotId")
        operation = str(request.get("operation") or "")
        if operation in {"market_snapshot", "market_breadth"} and not provider_snapshot_id:
            raise ExternalContractError("snapshot-producing response must provide concrete snapshot_id")
        requested_snapshot_id = request.get("snapshot_id")
        snapshot_id = provider_snapshot_id
        if snapshot_id is None and requested_snapshot_id and str(requested_snapshot_id).lower() not in _SNAPSHOT_SELECTORS:
            snapshot_id = str(requested_snapshot_id)
        kwargs = {
            "payload": payload,
            "as_of": as_of,
            "available_at": available_at,
            "subject_key": request.get("subject_key", "unknown"),
            "contract_version": contract_version,
            "quality_status": quality,
            "confidence": request.get("confidence", payload.get("confidence")),
            "freshness_seconds": request.get("freshness_seconds", payload.get("freshness_seconds")),
            "data_version": request.get("data_version") or payload.get("data_version") or payload.get("service_version"),
            "snapshot_id": snapshot_id,
            "source_ref": request.get("source_ref") or payload.get("source_ref") or f"{system.value}:{request.get('operation') or evidence_type.value.lower()}",
        }
        kwargs["subject_type"] = request.get("subject_type") or ("portfolio" if evidence_type in {EvidenceType.PORTFOLIO_POSITION, EvidenceType.PORTFOLIO_EXPOSURE, EvidenceType.PORTFOLIO_RISK} else "symbol" if system == SourceSystem.FACTOR else "subject" if system == SourceSystem.CONTENT else "market")
        if evidence_type in {EvidenceType.TECHNICAL_SIGNAL, EvidenceType.TECHNICAL_PROFILE, EvidenceType.LIQUIDITY}:
            kwargs["subject_type"] = "symbol"
        if evidence_type in {EvidenceType.PORTFOLIO_POSITION, EvidenceType.PORTFOLIO_EXPOSURE, EvidenceType.PORTFOLIO_RISK}:
            if system != SourceSystem.QUANT:
                raise GatewayRequestError("portfolio evidence must come from quant")
            try:
                item = validate_evidence(self.portfolio_adapter.to_evidence(evidence_type=evidence_type, **kwargs))
            except ValueError as exc:
                raise ExternalContractError(str(exc)) from exc
        else:
            adapter = {SourceSystem.QUANT: self.market_adapter, SourceSystem.FACTOR: self.factor_adapter, SourceSystem.CONTENT: self.content_adapter}[system]
            try:
                item = validate_evidence(adapter.to_evidence(evidence_type=evidence_type, **kwargs))
            except ValueError as exc:
                raise ExternalContractError(str(exc)) from exc
        max_age = request.get("max_age_seconds")
        if max_age is not None:
            quality = freshness_quality(item, decision_time=decision_time, max_age_seconds=int(max_age))
            if quality != item.quality_status:
                item = item.model_copy(update={"quality_status": quality})
        return item

    def _dispatch(self, system: SourceSystem, request: dict[str, Any], *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        operation = request.get("operation")
        if not operation or request.get("method") or request.get("path"):
            raise GatewayRequestError("gateway accepts only an allowlisted read operation, not method/path")
        client = {SourceSystem.QUANT: self.quant_client, SourceSystem.FACTOR: self.factor_client, SourceSystem.CONTENT: self.content_client}.get(system)
        if client is None:
            raise RuntimeError(f"{system.value} client unavailable")
        methods = {
            "market_snapshot": (SourceSystem.QUANT, "get_market_snapshot"),
            "market_breadth": (SourceSystem.QUANT, "get_market_snapshot"),
            "market_regime": (SourceSystem.QUANT, "get_market_regime"),
            "sector_strength": (SourceSystem.QUANT, "get_sector_strength"),
            "technical_evidence": (SourceSystem.QUANT, "get_technical_evidence"),
            "portfolio_snapshot": (SourceSystem.QUANT, "get_portfolio_snapshot"),
            "portfolio_risk": (SourceSystem.QUANT, "get_portfolio_risk_inputs"),
            "backtest": (SourceSystem.QUANT, "get_backtest"),
            "factor_score": (SourceSystem.FACTOR, "get_factor"),
            "factor_set": (SourceSystem.FACTOR, "list_factors"),
            "factor_evidence": (SourceSystem.FACTOR, "get_factor_evidence"),
            "factor_research": (SourceSystem.FACTOR, "get_factor_evidence"),
            "content_search": (SourceSystem.CONTENT, "search_video_knowledge"),
            "knowledge_unit": (SourceSystem.CONTENT, "get_knowledge_unit"),
            "content_catalyst": (SourceSystem.CONTENT, "search_video_knowledge"),
        }
        expected_system, method_name = methods.get(operation, (None, None))
        if expected_system != system or method_name is None:
            raise GatewayRequestError(f"unsupported evidence operation: {operation}")
        method = getattr(client, method_name, None)
        if method is None:
            raise RuntimeError(f"client does not provide {method_name}")
        param_allowlist = {
            "market_snapshot": {"snapshot_id"}, "market_breadth": {"snapshot_id"}, "market_regime": {"as_of"},
            "sector_strength": {"as_of", "limit"}, "technical_evidence": {"symbol", "as_of"},
            "portfolio_snapshot": {"account_id"}, "portfolio_risk": {"account_id"}, "backtest": {"backtest_id"},
            "factor_score": {"factor_id"}, "factor_set": {"limit"}, "factor_evidence": {"factor_id"},
            "factor_research": {"factor_id"}, "content_search": {"query", "filters", "limit", "intent"},
            "knowledge_unit": {"unit_id"}, "content_catalyst": {"query", "filters", "limit", "intent"},
        }
        params = {key: value for key, value in dict(request.get("params") or {}).items() if key in param_allowlist.get(operation, set())}
        if "subject_key" in request and operation in {"technical_evidence", "factor_evidence", "factor_score", "knowledge_unit", "backtest"}:
            params.setdefault("symbol" if operation == "technical_evidence" else "backtest_id" if operation == "backtest" else "factor_id" if operation in {"factor_evidence", "factor_score"} else "unit_id", request["subject_key"])
        if operation in {"market_snapshot", "market_breadth"} and request.get("snapshot_id"):
            params.setdefault("snapshot_id", request["snapshot_id"])
        metadata = dict(trace_context or {})
        request_metadata = request.get("trace_context")
        if isinstance(request_metadata, dict):
            metadata.update(request_metadata)
        allowed = {"trace_id", "decision_id", "bundle_id", "agent_run_id", "proposal_id", "snapshot_id"}
        metadata = {key: value for key, value in metadata.items() if key in allowed and value is not None}
        if trace is not None:
            metadata = {**trace.headers(), **metadata}
            trace = trace
        if trace is not None:
            params["trace"] = trace
        elif metadata:
            params["trace_context"] = metadata
        return _invoke_read_method(method, params)

    @staticmethod
    def _request_system(request: dict[str, Any]) -> SourceSystem:
        try:
            return SourceSystem(request.get("source_system", SourceSystem.QUANT))
        except ValueError as exc:
            raise GatewayRequestError("unsupported source system") from exc

    @staticmethod
    def _selector_snapshot_id(request: dict[str, Any]) -> str | None:
        value = request.get("snapshot_id")
        return None if value is None or str(value).lower() in _SNAPSHOT_SELECTORS else str(value)

    @staticmethod
    def _failure_status(exc: Exception) -> DependencyStatusValue:
        text = str(exc).lower()
        return DependencyStatusValue.STALE if "stale" in text or "look-ahead" in text else DependencyStatusValue.UNAVAILABLE

    @staticmethod
    def _reason_code(exc: Exception) -> str:
        if isinstance(exc, DependencyError):
            return exc.code
        text = str(exc).lower()
        if isinstance(exc, TimeoutError) or "timeout" in text:
            return "DEPENDENCY_TIMEOUT"
        if "contract" in text or "operation" in text:
            return "CONTRACT_MISMATCH"
        if "look-ahead" in text or "stale" in text:
            return "STALE_DATA"
        if "snapshot" in text or "evidence" in text:
            return "INVALID_SNAPSHOT"
        return "DEPENDENCY_UNAVAILABLE"

    @staticmethod
    def _dependency_failure(code: str) -> tuple[DependencyStatusValue, str]:
        if code == STALE_DATA:
            return DependencyStatusValue.STALE, STALE_DATA
        if code in {CONTRACT_MISMATCH, INVALID_SNAPSHOT}:
            return DependencyStatusValue.DEGRADED, code
        if code in {DEPENDENCY_TIMEOUT, DEPENDENCY_UNAVAILABLE}:
            return DependencyStatusValue.UNAVAILABLE, code
        return DependencyStatusValue.UNAVAILABLE, code or DEPENDENCY_UNAVAILABLE

    def _status(self, system: SourceSystem, status: DependencyStatusValue, *, contract_version: str | None = None, service_version: str | None = None, snapshot_id: str | None = None, reason_codes: list[str] | None = None) -> DependencyStatus:
        now = self._now()
        return DependencyStatus(system=system, status=status, checked_at=now, contract_version=contract_version, service_version=service_version, snapshot_id=snapshot_id, reason_codes=reason_codes or [])

    def _now(self) -> datetime:
        if self.clock is None:
            return datetime.now(UTC)
        value = self.clock() if callable(self.clock) else self.clock.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("gateway clock must return timezone-aware datetime")
        return value

    @staticmethod
    def _consolidate_statuses(statuses: list[DependencyStatus]) -> list[DependencyStatus]:
        priority = {DependencyStatusValue.UNAVAILABLE: 4, DependencyStatusValue.STALE: 3, DependencyStatusValue.DEGRADED: 2, DependencyStatusValue.OK: 1}
        result = {}
        for item in statuses:
            old = result.get(item.system)
            if old is None or priority[item.status] > priority[old.status]:
                if old is not None:
                    item = item.model_copy(update={"reason_codes": sorted(set(old.reason_codes + item.reason_codes)), "contract_version": item.contract_version or old.contract_version, "service_version": item.service_version or old.service_version, "snapshot_id": item.snapshot_id or old.snapshot_id})
                result[item.system] = item
            elif old is not None:
                result[item.system] = old.model_copy(update={"reason_codes": sorted(set(old.reason_codes + item.reason_codes)), "contract_version": old.contract_version or item.contract_version, "service_version": old.service_version or item.service_version, "snapshot_id": old.snapshot_id or item.snapshot_id})
        return [result[key] for key in sorted(result, key=lambda value: value.value)]


def _timestamp(value: Any, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evidence timestamps must be timezone-aware")
        return value
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("evidence timestamps must be timezone-aware")
    return parsed


def _default_type(system: SourceSystem) -> str:
    return {SourceSystem.QUANT: EvidenceType.MARKET_SNAPSHOT.value, SourceSystem.FACTOR: EvidenceType.FACTOR_SCORE.value, SourceSystem.CONTENT: EvidenceType.KNOWLEDGE_CLAIM.value}[system]


def _default_contract(system: SourceSystem) -> str:
    return {SourceSystem.QUANT: "market-data.v1", SourceSystem.FACTOR: "factor.v1", SourceSystem.CONTENT: "content.v1"}[system]


def _invoke_read_method(method, params: dict[str, Any]) -> Any:
    """Pass only supported controlled metadata; never expose arbitrary kwargs."""
    import inspect

    signature = inspect.signature(method)
    accepts_kwargs = any(item.kind == inspect.Parameter.VAR_KEYWORD for item in signature.parameters.values())
    if not accepts_kwargs:
        params = {key: value for key, value in params.items() if key in signature.parameters}
    return method(**params)


__all__ = ["EvidenceGateway", "ExternalContractError", "ExternalStaleError", "GatewayRequestError"]
