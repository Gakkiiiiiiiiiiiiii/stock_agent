from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import httpx

from financial_agent.utils import project_root


PROJECT_ROOT = project_root()
DEFAULT_QUANT_ROOT = PROJECT_ROOT.parent / "quant"
BRIDGE_TIMEOUT_SECONDS = 30
HISTORY_TIMEOUT_SECONDS = 300
INDUSTRY_MAP_TIMEOUT_SECONDS = 300
FINANCIAL_DATA_TIMEOUT_SECONDS = 300

logger = logging.getLogger(__name__)


class QmtBridgeError(RuntimeError):
    pass


class QmtBridgeClient:
    """通过独立 Python 3.6 进程访问 xtquant。"""

    def __init__(self) -> None:
        self.base_url = os.getenv("QMT_BRIDGE_BASE_URL", "").strip().rstrip("/")
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
        if self.base_url:
            try:
                return self._http_get("health")
            except QmtBridgeError as exc:
                if not self._http_fallback_enabled():
                    raise
                logger.warning("QMT HTTP bridge healthcheck failed; fallback to direct bridge: %s", exc)
        return self._run("health")

    def get_quotes(self, symbols: list[str]) -> dict[str, Any]:
        if not symbols:
            return {}
        if self.base_url:
            try:
                payload = self._http_post("quote", {"symbols": symbols}, timeout_seconds=BRIDGE_TIMEOUT_SECONDS)
                return payload.get("quotes", {}) or {}
            except QmtBridgeError as exc:
                if not self._http_fallback_enabled():
                    raise
                logger.warning("QMT HTTP bridge quote failed; fallback to direct bridge: %s", exc)
        payload = self._run("quote", "--symbols", ",".join(symbols))
        return payload.get("quotes", {}) or {}

    def get_industry_map(
        self,
        symbols: list[str] | None = None,
        sector_prefix: str = "GICS2",
        only_a_share: bool = True,
    ) -> list[dict[str, Any]]:
        if self.base_url:
            try:
                payload = self._http_post(
                    "industry-map",
                    {"symbols": symbols or [], "sector_prefix": sector_prefix, "only_a_share": only_a_share},
                    timeout_seconds=INDUSTRY_MAP_TIMEOUT_SECONDS,
                )
                return payload.get("rows", []) or []
            except QmtBridgeError as exc:
                if not self._http_fallback_enabled():
                    raise
                logger.warning("QMT HTTP bridge industry-map failed; fallback to direct bridge: %s", exc)
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
        if self.base_url:
            try:
                payload = self._http_post(
                    "history",
                    {
                        "symbols": symbols,
                        "period": period,
                        "start_time": start_time,
                        "end_time": end_time,
                        "dividend_type": dividend_type,
                        "fill_data": fill_data,
                        "prefer_cache_first": prefer_cache_first,
                    },
                    timeout_seconds=HISTORY_TIMEOUT_SECONDS,
                )
                return payload.get("rows", []) or []
            except QmtBridgeError as exc:
                if not self._http_fallback_enabled():
                    raise
                logger.warning("QMT HTTP bridge history failed; fallback to direct bridge: %s", exc)
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

    def get_financial_data(
        self,
        symbols: list[str],
        tables: list[str],
        start_date: str | None = None,
        end_date: str | None = None,
        report_type: str = "announce_time",
    ) -> dict[str, Any]:
        """获取财务数据（对应桥脚本 financial-data 命令）。

        返回 {symbol: {table: [row dict, ...]}}；表名如 PershareIndex/Income/
        Balance/Capital，行内日期字段（m_anntime/m_timetag）为 %Y%m%d 字符串。
        """
        if not symbols or not tables:
            return {}
        if self.base_url:
            try:
                payload = self._http_post(
                    "financial-data",
                    {
                        "symbols": symbols,
                        "tables": tables,
                        "start_time": start_date or "",
                        "end_time": end_date or "",
                        "report_type": report_type,
                    },
                    timeout_seconds=FINANCIAL_DATA_TIMEOUT_SECONDS,
                )
                return payload.get("data", {}) or {}
            except QmtBridgeError as exc:
                if not self._http_fallback_enabled():
                    raise
                logger.warning("QMT HTTP bridge financial-data failed; fallback to direct bridge: %s", exc)
        payload = self._run(
            "financial-data",
            "--symbols",
            ",".join(symbols),
            "--tables",
            ",".join(tables),
            "--start-time",
            start_date or "",
            "--end-time",
            end_date or "",
            "--report-type",
            report_type,
            timeout_seconds=FINANCIAL_DATA_TIMEOUT_SECONDS,
        )
        return payload.get("data", {}) or {}

    def _http_get(self, path: str, timeout_seconds: int = BRIDGE_TIMEOUT_SECONDS) -> dict[str, Any]:
        try:
            response = httpx.get(f"{self.base_url}/{path}", timeout=timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise QmtBridgeError(f"QMT HTTP bridge request failed: {exc}") from exc
        if not payload.get("ok"):
            raise QmtBridgeError(payload.get("error", "QMT HTTP bridge returned failure"))
        return payload.get("data", {}) or {}

    def _http_post(self, path: str, payload: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
        try:
            response = httpx.post(f"{self.base_url}/{path}", json=payload, timeout=timeout_seconds)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            raise QmtBridgeError(f"QMT HTTP bridge request failed: {exc}") from exc
        if not data.get("ok"):
            raise QmtBridgeError(data.get("error", "QMT HTTP bridge returned failure"))
        return data.get("data", {}) or {}

    def _run(self, command: str, *extra_args: str, timeout_seconds: int | None = None) -> dict[str, Any]:
        # §5.1：集成栈内 QMT 不作为内置服务；显式禁用时快速失败（交给上层降级），
        # 避免在无 QMT 环境中无限重试/阻塞决策链路。
        if os.getenv("QMT_BRIDGE_DISABLED", "").strip().lower() in {"1", "true", "yes"}:
            raise QmtBridgeError("QMT bridge disabled (QMT_BRIDGE_DISABLED)")
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

    @staticmethod
    def _http_fallback_enabled() -> bool:
        value = os.getenv("QMT_BRIDGE_HTTP_FALLBACK", "true").strip().lower()
        return value not in {"0", "false", "no", "off"}

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
