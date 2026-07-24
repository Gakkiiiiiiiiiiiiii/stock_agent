from __future__ import annotations

import argparse
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


class QmtHttpBridge:
    def __init__(self, args: argparse.Namespace) -> None:
        self.python = Path(args.bridge_python)
        self.script = Path(args.bridge_script)
        self.install_dir = Path(args.install_dir)
        self.userdata_dir = Path(args.userdata_dir)
        self.account_id = args.account_id or ""

    def run(self, command: str, extra_args: list[str], timeout: int = 300) -> dict:
        cmd = [
            str(self.python),
            str(self.script),
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
        completed = subprocess.run(cmd, capture_output=True, check=False, env=env, timeout=timeout)
        stdout = _decode(completed.stdout)
        stderr = _decode(completed.stderr)
        if completed.returncode != 0:
            return {"ok": False, "error": stderr or stdout or "qmt bridge failed"}
        json_start = stdout.find('{"ok"')
        if json_start > 0:
            stdout = stdout[json_start:]
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {"ok": False, "error": f"invalid bridge output: {stdout[:1000]}"}


def make_handler(bridge: QmtHttpBridge):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if urlparse(self.path).path == "/health":
                self._json(bridge.run("health", [], timeout=30))
            else:
                self._json({"ok": False, "error": "not found"}, status=404)

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("content-length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            path = urlparse(self.path).path
            if path == "/quote":
                result = bridge.run("quote", ["--symbols", ",".join(payload.get("symbols") or [])], timeout=30)
            elif path == "/history":
                result = bridge.run(
                    "history",
                    [
                        "--symbols", ",".join(payload.get("symbols") or []),
                        "--period", str(payload.get("period") or "1d"),
                        "--start-time", str(payload.get("start_time") or ""),
                        "--end-time", str(payload.get("end_time") or ""),
                        "--dividend-type", str(payload.get("dividend_type") or "front"),
                        "--fill-data", str(bool(payload.get("fill_data", True))).lower(),
                        "--prefer-cache-first", str(bool(payload.get("prefer_cache_first", True))).lower(),
                    ],
                    timeout=300,
                )
            elif path == "/industry-map":
                result = bridge.run(
                    "industry-map",
                    [
                        "--symbols", ",".join(payload.get("symbols") or []),
                        "--sector-prefix", str(payload.get("sector_prefix") or "GICS2"),
                        "--only-a-share", str(bool(payload.get("only_a_share", True))).lower(),
                    ],
                    timeout=300,
                )
            else:
                result = {"ok": False, "error": "not found"}
            self._json(result, status=200 if result.get("ok") else 500)

        def log_message(self, format, *args):  # noqa: A002
            return

        def _json(self, payload: dict, status: int = 200) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


def _decode(payload: bytes) -> str:
    for encoding in ("utf-8", "gb18030"):
        try:
            return payload.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="ignore").strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("QMT_HTTP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("QMT_HTTP_PORT", "18080")))
    parser.add_argument("--bridge-python", default=os.getenv("QMT_BRIDGE_PYTHON", "../quant/.venv-qmt36/Scripts/python.exe"))
    parser.add_argument("--bridge-script", default=os.getenv("QMT_BRIDGE_SCRIPT", "../quant/scripts/qmt_bridge.py"))
    parser.add_argument("--install-dir", default=os.getenv("QMT_INSTALL_DIR", "../quant/runtime/qmt_client/installed"))
    parser.add_argument("--userdata-dir", default=os.getenv("QMT_USERDATA_DIR", "../quant/runtime/qmt_client/installed/userdata_mini"))
    parser.add_argument("--account-id", default=os.getenv("QMT_ACCOUNT_ID", ""))
    args = parser.parse_args()
    bridge = QmtHttpBridge(args)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(bridge))
    print(f"QMT HTTP bridge listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
