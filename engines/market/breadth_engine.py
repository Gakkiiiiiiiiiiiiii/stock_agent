from __future__ import annotations


def compute_breadth(up_count: int, down_count: int) -> float:
    total = up_count + down_count
    return 0.5 if total == 0 else round(up_count / total, 4)

