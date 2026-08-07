from __future__ import annotations

import math


def recall_at_k(retrieved: list[str], expected: set[str], k: int) -> float:
    return len(set(retrieved[:k]) & expected) / len(expected) if expected else 1.0


def precision_at_k(retrieved: list[str], expected: set[str], k: int) -> float:
    return len(set(retrieved[:k]) & expected) / k if k else 0.0


def reciprocal_rank(retrieved: list[str], expected: set[str]) -> float:
    return next((1 / index for index, value in enumerate(retrieved, 1) if value in expected), 0.0)


def ndcg_at_k(retrieved: list[str], expected: set[str], k: int) -> float:
    dcg = sum(1 / math.log2(index + 1) for index, value in enumerate(retrieved[:k], 1) if value in expected)
    ideal = sum(1 / math.log2(index + 1) for index in range(1, min(len(expected), k) + 1))
    return dcg / ideal if ideal else 1.0
