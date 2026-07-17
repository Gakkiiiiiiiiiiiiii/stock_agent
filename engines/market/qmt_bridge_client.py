from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from financial_agent.utils import project_root


PROJECT_ROOT = project_root()
DEFAULT_QUANT_ROOT = PROJECT_ROOT.parent / "quant"
BRIDGE_TIMEOUT_SECONDS = 30
HISTORY_TIMEOUT_SECONDS = 300
INDUSTRY_MAP_TIMEOUT_SECONDS = 300


class QmtBridgeError(RuntimeError):
    pass


class QmtBridgeClient:
    """通过独立 Python 3.6 进程访问 xtquant。"""

    def __init__(self) -> None:
        self.python_path = self._resolve_path(
            os.getenv("QMT_BRIDGE_PYTHON"),
            DEFAULT_QUANT_ROOT / ".venv-qmt36" / "Scripts" / "python.exe",
        )
        self.script_path = self._resolve_path(
            os.getenv("QMT_BRIDGE_SCRIPT"),
            DEFAULT_QUANT_ROOT / "scripts" / "qmt_bridge.py",
        )
        self.install_dir = self._resolve_path(
            os.getenv("QMT_INSTALL_DIR"),
            DEFAULT_QUANT_ROOT / "runtime" / "qmt_client" / "installed",
        )
        self.userdata_dir = self._resolve_userdata_dir(
            os.getenv("QMT_USERDATA_DIR"),
            self.install_dir / "userdata_mini",
        )
        self.account_id = os.getenv("QMT_ACCOUNT_ID", "").strip()

    def healthcheck(self) -> dict[str, Any]:
        return self._run("health")

    def get_quotes(self, symbols: list[str]) -> dict[str, Any]:
        if not symbols:
            return {}
        payload = self._run("quote", "--symbols", ",".join(symbols))
        return payload.get("quotes", {}) or {}

    def get_industry_map(
        self,
        symbols: list[str] | None = None,
        sector_prefix: str = "GICS2",
        only_a_share: bool = True,
    ) -> list[dict[str, Any]]:
        payload = self._run(
            "industry-map",
            "--symbols",
            ",".join(symbols or []),
            "--sector-prefix",
            str(sector_prefix or "GICS2"),
            "--only-a-share",
            str(only_a_share).lower(),
            timeout_seconds=INDUSTRY_MAP_TIMEOUT_SECONDS,
        )
        return payload.get("rows", []) or []

    def get_history(
        self,
        symbols: list[str],
        period: str,
        start_time: str,
        end_time: str,
        dividend_type: str,
        fill_data: bool = True,
        prefer_cache_first: bool = True,
    ) -> list[dict[str, Any]]:
        if not symbols:
            return []
        payload = self._run(
            "history",
            "--symbols",
            ",".join(symbols),
            "--period",
            period,
            "--start-time",
            start_time,
            "--end-time",
            end_time,
            "--dividend-type",
            dividend_type,
            "--fill-data",
            str(fill_data).lower(),
            "--prefer-cache-first",
            str(prefer_cache_first).lower(),
            timeout_seconds=HISTORY_TIMEOUT_SECONDS,
        )
        return payload.get("rows", []) or []

    def _run(self, command: str, *extra_args: str, timeout_seconds: int | None = None) -> dict[str, Any]:
        self._ensure_runtime_paths()
        cmd = [
            str(self.python_path),
            str(self.script_path),
            command,
            "--install-dir",
            str(self.install_dir),
            "--userdata-dir",
            str(self.userdata_dir),
        ]
        if self.account_id:
            cmd.extend(["--account-id", self.account_id])
        cmd.extend(extra_args)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            completed = subprocess.run(
                cmd,
                cwd=PROJECT_ROOT,
                capture_output=True,
                check=False,
                env=env,
                timeout=timeout_seconds or BRIDGE_TIMEOUT_SECONDS,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired as exc:
            timeout_text = timeout_seconds or BRIDGE_TIMEOUT_SECONDS
            raise QmtBridgeError(f"QMT 桥接调用超时（>{timeout_text}s）: command={command}") from exc
        stdout = self._decode_output(completed.stdout)
        stderr = self._decode_output(completed.stderr)
        if completed.returncode != 0:
            detail = stderr or stdout or "桥接进程没有返回错误详情"
            raise QmtBridgeError(f"QMT 桥接执行失败: {detail}")
        json_start = stdout.find('{"ok"')
        if json_start > 0:
            stdout = stdout[json_start:]
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise QmtBridgeError(f"QMT 桥接返回了不可解析内容: {stdout}") from exc
        if not payload.get("ok"):
            raise QmtBridgeError(payload.get("error", "QMT 桥接调用失败"))
        return payload.get("data", {}) or {}

    def _ensure_runtime_paths(self) -> None:
        if not self.python_path.exists():
            raise QmtBridgeError(f"QMT 桥接 Python 不存在: {self.python_path}")
        if not self.script_path.exists():
            raise QmtBridgeError(f"QMT 桥接脚本不存在: {self.script_path}")
        self.install_dir = self._resolve_install_dir(self.install_dir)
        if not self.install_dir.exists():
            raise QmtBridgeError(f"QMT 安装目录不存在: {self.install_dir}")
        if not self.userdata_dir.exists():
            raise QmtBridgeError(f"QMT 用户数据目录不存在: {self.userdata_dir}")

    @staticmethod
    def _resolve_path(raw_path: str | None, default: Path) -> Path:
        chosen = Path(raw_path) if raw_path else default
        if chosen.is_absolute():
            return chosen
        return (PROJECT_ROOT / chosen).resolve()

    @staticmethod
    def _resolve_userdata_dir(raw_path: str | None, default: Path) -> Path:
        if raw_path:
            return QmtBridgeClient._resolve_path(raw_path, default)
        if default.exists():
            return default
        return default.parent / "userdata"

    @staticmethod
    def _resolve_install_dir(path: Path) -> Path:
        if path.exists():
            return path
        if path.name.lower() != "client":
            return path
        parent = path.parent
        for candidate_name in ("installed", "live_installed"):
            candidate = parent / candidate_name
            if candidate.exists():
                return candidate
        return path

    @staticmethod
    def _decode_output(payload: bytes | str) -> str:
        if isinstance(payload, str):
            return payload.strip()
        for encoding in ("utf-8", "gb18030"):
            try:
                return payload.decode(encoding).strip()
            except UnicodeDecodeError:
                continue
        return payload.decode("utf-8", errors="ignore").strip()
