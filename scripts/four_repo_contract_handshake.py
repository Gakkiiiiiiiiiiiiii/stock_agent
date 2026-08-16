"""Four-Repo Contract Handshake（P0 X-02）。

在 Main E2E Business 场景之前执行：任何 Provider/Consumer 契约不兼容，
Main E2E 立即失败。

Provider 声明（live /health/version）：
  - Quant:   market-data.v1 / backtest.v1 / trading.v1
  - Content: content-factor-signal.v3
  - Factor:  factor.v1（并作为 content-factor-signal.v3 consumer）

Agent consumer 支持（repo 内契约模块静态断言）：
  - content-factor-signal.v3 / factor.v1 / market-data.v1 / backtest.v1
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

QUANT = os.getenv("QUANT_SERVICE_URL", "http://localhost:8011")
CONTENT = os.getenv("CONTENT_SERVICE_URL", "http://localhost:8100")
FACTOR = os.getenv("FACTOR_SERVICE_URL", "http://localhost:8200")

# repo 根目录（保证 contracts/ 可导入：editable 包映射不一定包含 contracts）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

QUANT_REQUIRED = {"market-data.v1", "backtest.v1", "trading.v1"}
CONTENT_REQUIRED = {"content-factor-signal.v3"}
FACTOR_REQUIRED = {"factor.v1"}


def _step(name: str) -> None:
    print(f"[contract-handshake] {name}", flush=True)


def _contract_versions(client: httpx.Client, base_url: str, service: str) -> set[str]:
    response = client.get(f"{base_url}/health/version")
    response.raise_for_status()
    payload = response.json()
    versions = payload.get("contract_versions") or []
    assert versions, f"{service} /health/version must advertise contract_versions"
    return set(versions)


def check_providers(client: httpx.Client) -> None:
    quant = _contract_versions(client, QUANT, "quant")
    assert QUANT_REQUIRED <= quant, f"quant provider contracts missing: {sorted(QUANT_REQUIRED - quant)}"
    _step(f"quant OK: {sorted(QUANT_REQUIRED)}")

    content = _contract_versions(client, CONTENT, "content")
    assert CONTENT_REQUIRED <= content, f"content provider contracts missing: {sorted(CONTENT_REQUIRED - content)}"
    _step(f"content OK: {sorted(CONTENT_REQUIRED)}")

    factor = _contract_versions(client, FACTOR, "factor")
    assert FACTOR_REQUIRED <= factor, f"factor provider contracts missing: {sorted(FACTOR_REQUIRED - factor)}"
    # Factor 同时是 content signal consumer：main 必须声明 v3。
    assert "content-factor-signal.v3" in factor, "factor consumer must declare content-factor-signal.v3 on main"
    _step(f"factor OK: {sorted(FACTOR_REQUIRED)} + content-factor-signal.v3 consumer")


def check_agent_consumer() -> None:
    """Agent consumer 契约支持（静态断言 repo 内契约模块与契约文件）。"""
    from contracts.content import CONTENT_FACTOR_SIGNAL_VERSION
    from contracts.factor import FACTOR_API_VERSION

    assert CONTENT_FACTOR_SIGNAL_VERSION == "content-factor-signal.v3", "agent content consumer must default to v3"
    assert FACTOR_API_VERSION == "factor.v1", "agent factor consumer must use factor.v1"

    contracts_root = Path(__file__).resolve().parents[1] / "contracts"
    assert (contracts_root / "market-data.v1").is_dir(), "agent must carry market-data.v1 contract"

    # quant 侧 backtest.v1 契约必须存在（provider 仓 sibling checkout）。
    quant_contracts = Path(__file__).resolve().parents[2] / "quant" / "contracts"
    if quant_contracts.is_dir():
        assert (quant_contracts / "backtest.v1.yaml").exists(), "quant backtest.v1 contract missing"
        assert (quant_contracts / "trading.v1.yaml").exists(), "quant trading.v1 contract missing"
        assert (quant_contracts / "market-data.v1.yaml").exists(), "quant market-data.v1 contract missing"
        _step("quant repo contract files OK: market-data.v1 / backtest.v1 / trading.v1")
    else:
        _step("quant sibling checkout not present; skipped repo-file check (live handshake already verified)")
    _step("agent consumer OK: content-factor-signal.v3 / factor.v1 / market-data.v1 / backtest.v1")


def main() -> None:
    with httpx.Client(timeout=30) as client:
        check_providers(client)
    check_agent_consumer()
    _step("CONTRACT HANDSHAKE PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"[contract-handshake] FAILED: {exc}", file=sys.stderr)
        raise
