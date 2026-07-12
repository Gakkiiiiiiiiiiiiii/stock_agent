from __future__ import annotations


def render_daily_scan(report: dict) -> str:
    lines = [
        "# 每日市场扫描",
        "",
        f"- 日期：{report.get('date')}",
        f"- 模式：{report.get('mode')}",
        "",
        "## 市场环境",
    ]
    env = report.get("market_environment", {})
    lines.extend(f"- {key}: {value}" for key, value in env.items() if key != "warnings")
    lines.append("")
    lines.append("## 强主题")
    for item in report.get("top_themes", []):
        lines.append(f"- {item['theme']}：{item['score']}，{item['reason']}")
    lines.append("")
    lines.append("## 风险提示")
    lines.extend(f"- {item}" for item in env.get("warnings", []))
    return "\n".join(lines)

