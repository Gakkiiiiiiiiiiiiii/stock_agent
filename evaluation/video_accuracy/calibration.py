"""P2-3 Confidence Calibration（设计文档 §75/§86）：Reliability diagram / ECE / Brier。

纯标准库实现。输入为 (score, label) 对：score ∈ [0, 1] 为系统输出的置信分数，
label ∈ {0, 1} 为该条预测是否实际正确。

重要边界（§17/§75）：support_score 在通过 Golden Dataset 校准（ECE <= 0.05）之前
不得解释为概率（Probability of Correct Support），只能叫 score / proxy。
"""

from __future__ import annotations


def _normalize_pairs(pairs) -> list[tuple[float, int]]:
    normalized: list[tuple[float, int]] = []
    for score, label in pairs:
        value = float(score)
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"score out of [0, 1]: {score!r}")
        normalized.append((value, 1 if label else 0))
    return normalized


def reliability_bins(pairs, n_bins: int = 10) -> list[dict]:
    """Reliability diagram 分桶统计。

    返回等宽桶列表：[{"bin_start", "bin_end", "count", "mean_confidence",
    "mean_accuracy"}]，只包含非空桶。
    """
    normalized = _normalize_pairs(pairs)
    buckets: dict[int, list[tuple[float, int]]] = {}
    for score, label in normalized:
        index = min(int(score * n_bins), n_bins - 1)
        buckets.setdefault(index, []).append((score, label))
    result: list[dict] = []
    for index in sorted(buckets):
        items = buckets[index]
        count = len(items)
        result.append(
            {
                "bin_start": index / n_bins,
                "bin_end": (index + 1) / n_bins,
                "count": count,
                "mean_confidence": sum(score for score, _ in items) / count,
                "mean_accuracy": sum(label for _, label in items) / count,
            }
        )
    return result


def expected_calibration_error(pairs, n_bins: int = 10) -> float:
    """ECE：sum(|bin| / N * |mean_confidence - mean_accuracy|)。无样本返回 0.0。"""
    normalized = _normalize_pairs(pairs)
    if not normalized:
        return 0.0
    total = len(normalized)
    return sum(
        (bucket["count"] / total) * abs(bucket["mean_confidence"] - bucket["mean_accuracy"])
        for bucket in reliability_bins(normalized, n_bins)
    )


def brier_score(pairs) -> float:
    """Brier score：mean((score - label)^2)。无样本返回 0.0。"""
    normalized = _normalize_pairs(pairs)
    if not normalized:
        return 0.0
    return sum((score - label) ** 2 for score, label in normalized) / len(normalized)


__all__ = ["reliability_bins", "expected_calibration_error", "brier_score"]
