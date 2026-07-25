from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from engines.technical.models import TechnicalProfile
from engines.technical.registry import IndicatorRegistry


RULE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
ALLOWED_OPERATORS = {
    "all",
    "any",
    "not",
    "gt",
    "gte",
    "lt",
    "lte",
    "eq",
    "between",
    "cross_up",
    "cross_down",
    "rising",
    "falling",
    "count_true",
    "rolling_max",
    "rolling_min",
    "context_gte",
    "context_lte",
}
ARITY = {
    "not": 1,
    "gt": 2,
    "gte": 2,
    "lt": 2,
    "lte": 2,
    "eq": 2,
    "between": 3,
    "cross_up": 2,
    "cross_down": 2,
    "rising": 2,
    "falling": 2,
    "count_true": 2,
    "rolling_max": 2,
    "rolling_min": 2,
    "context_gte": 2,
    "context_lte": 2,
}
ALLOWED_CONTEXT_FIELDS = {
    "market_regime.score",
    "market_regime.risk_appetite_score",
    "sector.strength_score",
    "theme.strength_score",
    "liquidity.amount",
}
TEMPLATE_VAR_RE = re.compile(r"{([^{}]+)}")


@dataclass(frozen=True)
class RuleValidationError:
    rule_id: str
    path: str
    message: str


class RulePackValidationError(ValueError):
    def __init__(self, errors: list[RuleValidationError]) -> None:
        self.errors = errors
        detail = "; ".join(f"{item.rule_id} {item.path}: {item.message}" for item in errors)
        super().__init__(detail)


class RulePackValidator:
    def __init__(self, registry: IndicatorRegistry, max_depth: int = 12, max_nodes: int = 100) -> None:
        self.registry = registry
        self.max_depth = max_depth
        self.max_nodes = max_nodes

    def validate(self, pack_name: str, pack: dict[str, Any], profile: TechnicalProfile) -> dict[str, Any]:
        errors: list[RuleValidationError] = []
        profile_validation = self.registry.validate_profile(profile)
        if not profile_validation["valid"]:
            errors.extend(
                RuleValidationError(pack_name, "profile", message)
                for message in profile_validation["errors"]
            )
        references = self._indicator_references(profile)
        seen_ids: set[str] = set()
        for index, rule in enumerate(pack.get("rules") or []):
            rule_id = str(rule.get("id") or f"rules[{index}]")
            if rule_id in seen_ids:
                errors.append(RuleValidationError(rule_id, "id", "duplicate rule id"))
            seen_ids.add(rule_id)
            version = str(rule.get("version") or "1.0.0")
            if not RULE_VERSION_RE.match(version):
                errors.append(RuleValidationError(rule_id, "version", "rule version must be semver"))
            score = rule.get("score", 0)
            if not isinstance(score, (int, float)) or score < 0 or score > 100:
                errors.append(RuleValidationError(rule_id, "score", "score must be between 0 and 100"))
            condition = rule.get("condition")
            if not isinstance(condition, dict):
                errors.append(RuleValidationError(rule_id, "condition", "condition must be an AST object"))
            else:
                self._validate_ast(rule_id, "condition", condition, references, errors, depth=0, nodes=[0])
            for ev_index, item in enumerate(rule.get("evidence") or []):
                template = str((item or {}).get("template") or "")
                for variable in TEMPLATE_VAR_RE.findall(template):
                    if variable not in references and variable not in ALLOWED_CONTEXT_FIELDS:
                        errors.append(RuleValidationError(rule_id, f"evidence[{ev_index}]", f"unknown template variable: {variable}"))
        if errors:
            raise RulePackValidationError(errors)
        return {
            "valid": True,
            "rule_pack_hash": stable_rule_pack_hash(pack_name, pack),
            "profile_hash": self.registry.fingerprint(profile),
        }

    def _validate_ast(
        self,
        rule_id: str,
        path: str,
        expr: Any,
        references: set[str],
        errors: list[RuleValidationError],
        depth: int,
        nodes: list[int],
    ) -> None:
        nodes[0] += 1
        if depth > self.max_depth:
            errors.append(RuleValidationError(rule_id, path, f"AST depth exceeds {self.max_depth}"))
            return
        if nodes[0] > self.max_nodes:
            errors.append(RuleValidationError(rule_id, path, f"AST node count exceeds {self.max_nodes}"))
            return
        if not isinstance(expr, dict) or len(expr) != 1:
            errors.append(RuleValidationError(rule_id, path, "AST node must be a single-operator object"))
            return
        op, args = next(iter(expr.items()))
        if op not in ALLOWED_OPERATORS:
            errors.append(RuleValidationError(rule_id, path, f"unsupported operator: {op}"))
            return
        if op in {"all", "any"}:
            if not isinstance(args, list) or not args:
                errors.append(RuleValidationError(rule_id, path, f"{op} requires a non-empty list"))
                return
            for index, child in enumerate(args):
                self._validate_ast(rule_id, f"{path}.{op}[{index}]", child, references, errors, depth + 1, nodes)
            return
        if not isinstance(args, list) or len(args) != ARITY[op]:
            errors.append(RuleValidationError(rule_id, path, f"{op} requires {ARITY[op]} arguments"))
            return
        if op.startswith("context_"):
            if str(args[0]) not in ALLOWED_CONTEXT_FIELDS:
                errors.append(RuleValidationError(rule_id, path, f"context field not allowed: {args[0]}"))
            return
        for index, token in enumerate(args):
            if isinstance(token, (int, float)):
                continue
            if isinstance(token, dict):
                self._validate_ast(rule_id, f"{path}.{op}[{index}]", token, references, errors, depth + 1, nodes)
                continue
            if str(token) not in references:
                errors.append(RuleValidationError(rule_id, f"{path}.{op}[{index}]", f"unknown indicator reference: {token}"))

    def _indicator_references(self, profile: TechnicalProfile) -> set[str]:
        refs: set[str] = set()
        for spec in profile.indicators:
            definition = self.registry.get(spec.name)
            refs.add(spec.alias)
            for column in definition.output_schema:
                if column != "value":
                    refs.add(f"{spec.alias}.{column}")
        return refs


def stable_rule_pack_hash(pack_name: str, pack: dict[str, Any]) -> str:
    payload = {"name": pack_name, **pack}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
