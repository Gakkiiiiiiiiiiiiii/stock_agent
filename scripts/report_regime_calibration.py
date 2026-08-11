"""打印/导出 Regime × 策略历史校准统计表。

用法:
    .venv/Scripts/python.exe scripts/report_regime_calibration.py [--output artifacts/regime_calibration.json] [--min-samples 1]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from engines.regime.calibration import DEFAULT_HORIZONS, compute_regime_strategy_stats  # noqa: E402
from financial_agent.config import load_yaml_config  # noqa: E402
from storage.bootstrap import create_all  # noqa: E402

DEFAULT_OUTPUT = PROJECT_ROOT / "artifacts" / "regime_calibration.json"


def _calibration_config() -> dict:
    try:
        data = load_yaml_config("market_regime_thresholds.yaml")
    except FileNotFoundError:
        return {}
    return dict(data.get("calibration") or {})


def main() -> int:
    parser = argparse.ArgumentParser(description="Regime x strategy historical calibration report")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSON 输出路径")
    parser.add_argument("--min-samples", type=int, default=None, help="样本数下限（默认读配置 calibration.min_samples）")
    args = parser.parse_args()

    config = _calibration_config()
    min_samples = args.min_samples if args.min_samples is not None else int(config.get("min_samples", 5))
    horizons = tuple(int(h) for h in (config.get("horizons") or DEFAULT_HORIZONS))

    create_all()
    stats = compute_regime_strategy_stats(horizons=horizons)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "min_samples": min_samples,
        "horizons": list(horizons),
        "row_count": len(stats),
        "stats": stats,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    header = f"{'regime':<22} {'strategy':<24} {'h':>3} {'n':>5} {'mean':>9} {'median':>9} {'hit':>6}"
    print(header)
    print("-" * len(header))
    for row in stats:
        marker = "" if row["sample_size"] >= min_samples else " (n<min)"
        print(
            f"{row['market_regime']:<22} {row['strategy_key']:<24} {row['horizon_days']:>3} "
            f"{row['sample_size']:>5} {row['mean_excess_return']:>9.4f} {row['median_excess_return']:>9.4f} "
            f"{row['hit_rate']:>6.2f}{marker}"
        )
    print(f"\n{len(stats)} rows written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
