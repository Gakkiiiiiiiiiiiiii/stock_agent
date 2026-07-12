from __future__ import annotations


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

