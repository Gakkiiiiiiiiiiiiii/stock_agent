"""Constrained Strategy Factory; only DSL/config candidates cross this boundary."""
from __future__ import annotations

from financial_agent.config import load_yaml_config

from engines.strategy_factory.models import StrategyDefinition, StrategyStatus
from engines.strategy_factory.evaluation_runner import StrategyEvaluationRunner


def load_strategy_promotion_config() -> dict:
    try:
        return dict(load_yaml_config("strategy_promotion.yaml").get("strategy_promotion") or {})
    except FileNotFoundError:
        return {"min_oos_days": 60, "min_paper_days": 20, "max_drawdown": .15, "max_turnover": 4., "min_excess_sharpe": .5, "max_pbo": .35}


class StrategyFactory:
    def __init__(self, config: dict | None = None, evaluation_runner: StrategyEvaluationRunner | None = None, paper_evidence_provider=None) -> None:
        self.config = {**load_strategy_promotion_config(), **(config or {})}
        self._strategies: dict[str, StrategyDefinition] = {}
        self.evaluation_runner = evaluation_runner or StrategyEvaluationRunner()
        self.paper_evidence_provider = paper_evidence_provider or (lambda _strategy_id: {"observed_trading_days": 0, "passed": False})

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

    def run_evaluation(self, strategy_id: str, dataset: dict, evaluation_type: str = "BACKTEST") -> dict:
        return self.evaluation_runner.run(self._get(strategy_id), dataset, evaluation_type)

    def evaluate_backtest(self, strategy_id: str, evaluation_run_id: str) -> StrategyDefinition:
        strategy = self._get(strategy_id)
        if strategy.status != StrategyStatus.STATIC_VALIDATED:
            raise ValueError("strategy must pass static validation first")
        evaluation = self._evaluation(strategy_id, evaluation_run_id, "BACKTEST")
        metrics = evaluation["metrics"]
        if not evaluation["passed"] or float(metrics.get("max_drawdown", 1)) > float(self.config["max_drawdown"]) or float(metrics.get("turnover", float("inf"))) > float(self.config["max_turnover"]):
            strategy.status = StrategyStatus.REJECTED
        else:
            strategy.status = StrategyStatus.BACKTESTED
        return strategy

    def validate_oos(self, strategy_id: str, evaluation_run_id: str) -> StrategyDefinition:
        strategy = self._get(strategy_id)
        if strategy.status != StrategyStatus.BACKTESTED:
            raise ValueError("strategy must pass backtest first")
        evaluation = self._evaluation(strategy_id, evaluation_run_id, "OOS")
        metrics = evaluation["metrics"]
        ok = evaluation["passed"] and (int(metrics.get("oos_days", 0)) >= int(self.config["min_oos_days"]) and float(metrics.get("excess_sharpe", -99)) >= float(self.config["min_excess_sharpe"]) and float(metrics.get("pbo", 1)) <= float(self.config["max_pbo"]))
        strategy.status = StrategyStatus.OOS_VALIDATED if ok else StrategyStatus.REJECTED
        return strategy

    def paper_track(self, strategy_id: str) -> StrategyDefinition:
        strategy = self._get(strategy_id)
        if strategy.status != StrategyStatus.OOS_VALIDATED:
            raise ValueError("strategy must pass OOS validation first")
        evidence = self.paper_evidence_provider(strategy_id)
        strategy.status = StrategyStatus.PAPER_TRACKING if evidence.get("passed") and int(evidence.get("observed_trading_days", 0)) >= int(self.config["min_paper_days"]) else StrategyStatus.REJECTED
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

    def _evaluation(self, strategy_id: str, evaluation_run_id: str, expected_type: str) -> dict:
        evaluation = self.evaluation_runner.get(evaluation_run_id)
        if evaluation is None or evaluation.get("strategy_id") != strategy_id or evaluation.get("evaluation_type") != expected_type:
            raise ValueError("strategy evaluation run is missing or incompatible")
        return evaluation
