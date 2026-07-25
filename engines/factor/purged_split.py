from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PurgedWindow:
    train: tuple[int, int]
    validation: tuple[int, int]
    test: tuple[int, int]
    purge: int
    embargo: int


def build_purged_windows(n_days: int, horizon: int = 5, n_windows: int = 3, embargo: int | None = None) -> list[PurgedWindow]:
    purge = int(horizon)
    embargo = purge if embargo is None else int(embargo)
    min_block = purge + embargo + horizon + horizon + 2
    if n_days < min_block * 2:
        return []
    step = max(1, (n_days - min_block) // max(n_windows, 1))
    windows: list[PurgedWindow] = []
    for start in range(0, n_days - min_block + 1, step):
        train_end = start + step
        val_start = train_end + purge
        val_end = val_start + horizon
        test_start = val_end + embargo
        test_end = min(test_start + horizon, n_days - horizon)
        if test_end - test_start < horizon:
            continue
        windows.append(PurgedWindow((start, train_end), (val_start, val_end), (test_start, test_end), purge, embargo))
        if len(windows) >= n_windows:
            break
    return windows
