"""四仓集成栈 Fixture 数据 Seed（P0 X-07）。

§5.3：Integration/CI 使用 Fixture 历史数据，不依赖真实 QMT。
在 quant-data volume 内生成确定性历史行情（/data/history.parquet），
供 quant fixture market source 使用。幂等：文件存在即跳过。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

COMPOSE_FILE = str(Path(__file__).resolve().parents[1] / "deploy" / "integration" / "docker-compose.yml")
SYMBOLS = ["600519.SH", "000001.SZ"]
DAYS = 900  # 覆盖 business E2E 的 730d factor 窗口

SEED_CODE = (
    "from pathlib import Path\n"
    "from quant_demo.marketdata.ingestion import generate_sample_history\n"
    "target = Path('/data/history.parquet')\n"
    "if target.exists():\n"
    "    print('fixture history already present')\n"
    "else:\n"
    f"    generate_sample_history({SYMBOLS!r}, target, days={DAYS})\n"
    "    print('fixture history seeded:', target)\n"
)


def main() -> None:
    result = subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "exec", "-T", "quant", "python", "-c", SEED_CODE],
        capture_output=True,
        text=True,
    )
    sys.stdout.write(result.stdout)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(f"[four-repo-seed-fixture] FAILED (exit={result.returncode})")
    print("[four-repo-seed-fixture] OK")


if __name__ == "__main__":
    main()
