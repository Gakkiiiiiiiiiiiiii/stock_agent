"""Constrained Strategy Factory; only DSL/config candidates cross this boundary."""
from __future__ import annotations

from financial_agent.config import load_yaml_config

from engines.strategy_factory.models import StrategyDefinition, StrategyStatus


def load_strategy_promotion_config() -> dict:
    try:
        return dict(load_yaml_config("strategy_promotion.yaml").get("strategy_promotion") or {})
    except FileNotFoundError:
        return {"min_oos_days": 60, "min_paper_days": 20, "max_drawdown": .15, "max_turnover": 4., "min_excess_sharpe": .5, "max_pbo": .35}


class StrategyFactory:
    def __init__(self, config: dict | None = None) -> None:
        self.config = {**load_strategy_promotion_config(), **(config or {})}
        self._strategies: dict[str, StrategyDefinition] = {}

    def generate(self, definition: StrategyDefinition) -> StrategyDefinition:
        self._validate_schema(definition)
        definition.status = StrategyStatus.GENERATED
        self._strategies[definition.strategy_id] = definition
        return definition

    @staticmethod
    def _validate_schema(definition: StrategyDefinition) -> None:
        if not definition.entry_rules or not definition.exit_rules:
            raise ValueError("strategy requires entry_rules and exit_rules")
        execution = definition.execution
        if execution.get("signal_at") != "close" or execution.get("fill_at") != "next_open":
            raise ValueError("strategy must use close signal and next_open fill")
        # Do not scan the serialized top-level object for ``exec``: the valid
        # ``execution`` field would otherwise be a false positive.  Restrict
        # the ban to signals that may appear in user supplied DSL values.
        blob = str({"universe": definition.universe, "entry_rules": definition.entry_rules, "exit_rules": definition.exit_rules, "ranking": definition.ranking}).lower()
        banned = {"python", "eval(", "exec(", "lookahead", "future_data"}
        if any(item in blob for item in banned):
            raise ValueError("strategy contains non-DSL or lookahead expression")

    def validate_static(self, strategy_id: str, coverage_ok: bool = True) -> StrategyDefinition:
        strategy = self._get(strategy_id)
        if strategy.status != StrategyStatus.GENERATED:
            raise ValueError("strategy must be generated first")
        strategy.status = StrategyStatus.STATIC_VALIDATED if coverage_ok else StrategyStatus.REJECTED
        return strategy

    def evaluate_backtest(self, strategy_id: str, metrics: dict) -> StrategyDefinition:
        strategy = self._get(strategy_id)
        if strategy.status != StrategyStatus.STATIC_VALIDATED:
            raise ValueError("strategy must pass static validation first")
        if float(metrics.get("max_drawdown", 1)) > float(self.config["max_drawdown"]) or float(metrics.get("turnover", float("inf"))) > float(self.config["max_turnover"]):
            strategy.status = StrategyStatus.REJECTED
        else:
            strategy.status = StrategyStatus.BACKTESTED
        return strategy

    def validate_oos(self, strategy_id: str, metrics: dict) -> StrategyDefinition:
        strategy = self._get(strategy_id)
        if strategy.status != StrategyStatus.BACKTESTED:
            raise ValueError("strategy must pass backtest first")
        ok = (int(metrics.get("oos_days", 0)) >= int(self.config["min_oos_days"]) and float(metrics.get("excess_sharpe", -99)) >= float(self.config["min_excess_sharpe"]) and float(metrics.get("pbo", 1)) <= float(self.config["max_pbo"]))
        strategy.status = StrategyStatus.OOS_VALIDATED if ok else StrategyStatus.REJECTED
        return strategy

    def paper_track(self, strategy_id: str, days: int) -> StrategyDefinition:
        strategy = self._get(strategy_id)
        if strategy.status != StrategyStatus.OOS_VALIDATED:
            raise ValueError("strategy must pass OOS validation first")
        strategy.status = StrategyStatus.PAPER_TRACKING if days >= int(self.config["min_paper_days"]) else StrategyStatus.REJECTED
        return strategy

    def promote_shadow(self, strategy_id: str) -> StrategyDefinition:
        strategy = self._get(strategy_id)
        if strategy.status != StrategyStatus.PAPER_TRACKING:
            raise ValueError("strategy must complete paper tracking first")
        strategy.status = StrategyStatus.SHADOW
        return strategy

    def activate(self, strategy_id: str, execution_mode: str) -> StrategyDefinition:
        strategy = self._get(strategy_id)
        if strategy.status != StrategyStatus.SHADOW:
            raise ValueError("strategy must complete shadow mode first")
        if execution_mode != "LIVE":
            raise ValueError("strategy can only become ACTIVE for LIVE execution")
        strategy.status = StrategyStatus.ACTIVE
        return strategy

    def monitor(self, strategy_id: str, metrics: dict) -> StrategyDefinition:
        strategy = self._get(strategy_id)
        if strategy.status != StrategyStatus.ACTIVE:
            return strategy
        if float(metrics.get("max_drawdown", 0)) > float(self.config["max_drawdown"]):
            strategy.status = StrategyStatus.DECAYING
        if metrics.get("retire") or float(metrics.get("excess_sharpe", 0)) < 0:
            strategy.status = StrategyStatus.RETIRED
        return strategy

    def _get(self, strategy_id: str) -> StrategyDefinition:
        if strategy_id not in self._strategies:
            raise KeyError(strategy_id)
        return self._strategies[strategy_id]
