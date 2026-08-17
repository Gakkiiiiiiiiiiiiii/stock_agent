"""四仓集成 Compose 栈就绪检查（收尾文档 §43/§45）。

只等待四个权威服务；旧 Agent Market Data Service（8012）/ 旧 Agent Backtest
Runtime / 旧 Factor Paper Authority 不在栈内 —— 若仍有代码依赖旧路径，
four_repo_business_e2e.py 会直接失败。
"""

from __future__ import annotations

import os
import time
from urllib.error import URLError
from urllib.request import urlopen


def _endpoints() -> tuple[str, ...]:
    return (
        f"{os.getenv('AGENT_SERVICE_URL', 'http://localhost:8000')}/health/ready",
        f"{os.getenv('CONTENT_SERVICE_URL', 'http://localhost:8100')}/healthz",
        f"{os.getenv('FACTOR_SERVICE_URL', 'http://localhost:8200')}/healthz",
        f"{os.getenv('QUANT_SERVICE_URL', 'http://localhost:8011')}/health",
    )


def main() -> None:
    endpoints = _endpoints()
    deadline = time.monotonic() + 300
    pending = set(endpoints)
    while pending and time.monotonic() < deadline:
        for endpoint in tuple(pending):
            try:
                with urlopen(endpoint, timeout=30) as response:  # fixed local Compose endpoints
                    if response.status < 500:
                        pending.remove(endpoint)
            except (URLError, OSError, TimeoutError):
                # 启动中的服务可能直接断开连接（RemoteDisconnected 等）：继续重试。
                pass
        if pending:
            time.sleep(2)
    if pending:
        raise SystemExit(f"services did not become ready: {sorted(pending)}")
    print(f"[wait-four-repo-stack] ready: {sorted(endpoints)}")


if __name__ == "__main__":
    main()
