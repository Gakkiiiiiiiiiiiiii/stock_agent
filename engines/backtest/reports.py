from __future__ import annotations

from engines.backtest.metrics import calc_portfolio_metrics


def render_backtest_report(result: dict) -> str:
    metrics = result.get("metrics", {})
    return "\n".join(
        [
            "# 策略回测报告",
            "",
            f"- 总收益率：{metrics.get('total_return', 0):.2%}",
            f"- 最大回撤：{metrics.get('max_drawdown', 0):.2%}",
            f"- 交易次数：{metrics.get('trade_count', 0)}",
            f"- 胜率：{metrics.get('win_rate') if metrics.get('win_rate') is not None else '暂无'}",
        ]
    )


def _pct(value: float | None) -> str:
    return f"{value:.2%}" if value is not None else "暂无"


def render_portfolio_report(result: dict) -> str:
    """渲染 TopK 组合回测报告：净值 vs 基准、超额收益与月度收益表（Markdown）。"""
    metrics = result.get("metrics") or calc_portfolio_metrics(
        result.get("equity_curve", []),
        benchmark_curve=result.get("benchmark_curve"),
        trades=result.get("trades"),
        daily_turnover=result.get("daily_turnover"),
        dates=result.get("dates"),
    )
    equity = result.get("equity_curve") or []
    benchmark = result.get("benchmark_curve") or []

    lines = [
        "# 组合回测报告",
        "",
        "## 核心指标",
        "",
        f"- 期末净值：{equity[-1]:,.2f}" if equity else "- 期末净值：暂无",
        f"- 基准期末净值：{benchmark[-1]:,.2f}" if benchmark else "- 基准期末净值：暂无",
        f"- 总收益率：{_pct(metrics.get('total_return'))}",
        f"- 年化收益：{_pct(metrics.get('annual_return'))}",
        f"- 年化波动：{_pct(metrics.get('annual_vol'))}",
        f"- 夏普比率：{metrics.get('sharpe', 0):.2f}",
        f"- 最大回撤：{_pct(metrics.get('max_drawdown'))}",
        f"- 超额年化收益（对基准）：{_pct(metrics.get('excess_annual_return'))}",
        f"- 平均日换手：{_pct(metrics.get('avg_daily_turnover'))}",
        f"- 交易次数：{metrics.get('trade_count', 0)}",
        f"- 往返胜率：{_pct(metrics.get('win_rate'))}",
    ]

    monthly = metrics.get("monthly_returns") or {}
    if monthly:
        lines += [
            "",
            "## 月度收益",
            "",
            "| 月份 | 收益 |",
            "| --- | --- |",
        ]
        lines += [f"| {month} | {ret:.2%} |" for month, ret in sorted(monthly.items())]

    return "\n".join(lines)

