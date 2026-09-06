"""Fail-closed resolution for repository-owned contract artifacts."""
from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


class ContractPathError(ValueError):
    """A manifest reference does not name a repository contract artifact."""


def resolve_contract_file(repository_root: Path, path: str | Path) -> Path:
    """Resolve *path* only when its real location remains under ``contracts/``."""
    try:
        contracts_root = (Path(repository_root) / "contracts").resolve()
        resolved_path = Path(path).resolve()
    except OSError as exc:
        raise ContractPathError("CONTRACT_PATH_UNRESOLVABLE") from exc
    try:
        resolved_path.relative_to(contracts_root)
    except ValueError as exc:
        raise ContractPathError("CONTRACT_PATH_OUTSIDE_ROOT") from exc
    return resolved_path


def resolve_contract_reference(repository_root: Path, reference: str | Path) -> Path:
    """Resolve a manifest reference without accepting absolute or traversal input."""
    raw_reference = str(reference)
    windows_path = PureWindowsPath(raw_reference)
    posix_path = PurePosixPath(raw_reference)
    if not raw_reference:
        raise ContractPathError("CONTRACT_PATH_EMPTY")
    if Path(raw_reference).is_absolute() or windows_path.drive or windows_path.root or posix_path.is_absolute():
        raise ContractPathError("CONTRACT_PATH_ABSOLUTE")
    if ".." in windows_path.parts or ".." in posix_path.parts:
        raise ContractPathError("CONTRACT_PATH_TRAVERSAL")
    return resolve_contract_file(Path(repository_root), Path(repository_root) / raw_reference)
