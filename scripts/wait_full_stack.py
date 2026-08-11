from __future__ import annotations
import argparse, time
import httpx

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--url", action="append", required=True); parser.add_argument("--timeout", type=int, default=120); args = parser.parse_args()
    end = time.time() + args.timeout
    while time.time() < end:
        if all(_ready(url) for url in args.url): return 0
        time.sleep(2)
    return 1
def _ready(url: str) -> bool:
    try: return httpx.get(url, timeout=3).status_code < 400
    except Exception: return False
if __name__ == "__main__": raise SystemExit(main())
