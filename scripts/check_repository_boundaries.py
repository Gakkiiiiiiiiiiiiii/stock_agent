"""Conservative repository boundary scan; reports violations without deleting files."""
from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN = {"broker", "qmt", "order_submission"}


def scan(root: Path) -> list[str]:
    errors: list[str] = []
    scan_roots = [root / "app" / "domain", root / "app" / "routers" / "analysis_v2.py", root / "app" / "routers" / "agent.py", root / "financial_agent"]
    paths = [item for entry in scan_roots for item in (entry.rglob("*.py") if entry.is_dir() else [entry])]
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            errors.append(f"{path}: {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                text = ast.unparse(node).lower()
                if any(item in text for item in FORBIDDEN):
                    errors.append(f"{path}:{node.lineno}: forbidden dependency")
    return errors


if __name__ == "__main__":
    problems = scan(Path(__file__).resolve().parents[1])
    print("\n".join(problems))
    raise SystemExit(1 if problems else 0)
