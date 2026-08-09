"""Golden Video Accuracy Dataset 标注样本结构定义与校验（P2-1，设计文档 §72-73）。

数据集为 JSONL，一行一个样本（一个视频一条）。字段规范详见 README.md；
本模块是结构校验的单一事实来源，benchmark 与标注工具都必须经 load_dataset 进入。
"""

from __future__ import annotations

import json
from pathlib import Path

# §73 覆盖类型清单。
VIDEO_TYPES = ("单人口播", "K线技术分析", "PPT/财报", "行业研究", "宏观", "访谈", "直播", "低音质")

TRUTH_STATUSES = frozenset({"NOT_CHECKED", "NOT_FOUND", "EXTERNALLY_VERIFIED", "EXTERNAL_CONFLICT"})
SUPPORT_LABELS = frozenset({"SUPPORTED", "CONTRADICTED", "NOT_ENOUGH_EVIDENCE"})
NUMERIC_UNITS = frozenset({"PERCENT", "MULTIPLE", "CNY", "CNY_YI", "CNY_WAN", "POINT"})
COMPARATORS = frozenset({"GT", "GTE", "LT", "LTE", "EQ", "APPROX"})

_REQUIRED_SAMPLE_FIELDS = ("id", "video_id", "video_type", "ground_truth_transcript", "entities", "numbers", "speakers", "claims")
_REQUIRED_CLAIM_FIELDS = ("claim_id", "statement", "support_label", "truth_status", "negation", "evidence_span")


class AnnotationError(ValueError):
    """标注样本结构不合法。"""


def _err(sample_id: str, message: str) -> AnnotationError:
    return AnnotationError(f"sample {sample_id or '<unknown>'}: {message}")


def validate_sample(sample: dict) -> dict:
    """校验单条标注样本，不合法抛 AnnotationError，合法原样返回。"""
    if not isinstance(sample, dict):
        raise AnnotationError(f"sample must be an object, got {type(sample).__name__}")
    sample_id = str(sample.get("id") or "")
    for field in _REQUIRED_SAMPLE_FIELDS:
        if field not in sample:
            raise _err(sample_id, f"missing required field: {field}")
    if sample["video_type"] not in VIDEO_TYPES:
        raise _err(sample_id, f"video_type must be one of {VIDEO_TYPES}, got {sample['video_type']!r}")
    if not str(sample["ground_truth_transcript"]).strip():
        raise _err(sample_id, "ground_truth_transcript must be non-empty")

    entities = sample["entities"]
    if not isinstance(entities, list):
        raise _err(sample_id, "entities must be a list")
    for entity in entities:
        if not isinstance(entity, dict) or not str(entity.get("name") or "").strip():
            raise _err(sample_id, "each entity requires a non-empty name")
        ticker = entity.get("ticker")
        if ticker is not None and not str(ticker).isdigit():
            raise _err(sample_id, f"entity ticker must be digits, got {ticker!r}")

    numbers = sample["numbers"]
    if not isinstance(numbers, list):
        raise _err(sample_id, "numbers must be a list")
    for number in numbers:
        if not isinstance(number, dict) or not str(number.get("raw_expression") or "").strip():
            raise _err(sample_id, "each number requires raw_expression")
        if number.get("value") is None and number.get("min_value") is None:
            raise _err(sample_id, f"number {number.get('raw_expression')!r} requires value or min_value/max_value")
        unit = number.get("unit")
        if unit is not None and unit not in NUMERIC_UNITS:
            raise _err(sample_id, f"number unit must be one of {sorted(NUMERIC_UNITS)}, got {unit!r}")
        comparator = number.get("comparator")
        if comparator is not None and comparator not in COMPARATORS:
            raise _err(sample_id, f"number comparator must be one of {sorted(COMPARATORS)}, got {comparator!r}")

    speakers = sample["speakers"]
    if not isinstance(speakers, list) or any(not isinstance(s, dict) or not s.get("speaker_id") for s in speakers):
        raise _err(sample_id, "speakers must be a list of {speaker_id, ...}")

    claims = sample["claims"]
    if not isinstance(claims, list) or not claims:
        raise _err(sample_id, "claims must be a non-empty list")
    for claim in claims:
        if not isinstance(claim, dict):
            raise _err(sample_id, "each claim must be an object")
        for field in _REQUIRED_CLAIM_FIELDS:
            if field not in claim:
                raise _err(sample_id, f"claim {claim.get('claim_id')!r} missing field: {field}")
        if claim["support_label"] not in SUPPORT_LABELS:
            raise _err(sample_id, f"claim {claim['claim_id']!r} support_label must be one of {sorted(SUPPORT_LABELS)}")
        if claim["truth_status"] not in TRUTH_STATUSES:
            raise _err(sample_id, f"claim {claim['claim_id']!r} truth_status must be one of {sorted(TRUTH_STATUSES)}")
        if claim["negation"] is not None and not isinstance(claim["negation"], bool):
            raise _err(sample_id, f"claim {claim['claim_id']!r} negation must be true/false/null")
        span = claim["evidence_span"]
        if not isinstance(span, dict) or span.get("start_ms") is None or span.get("end_ms") is None:
            raise _err(sample_id, f"claim {claim['claim_id']!r} evidence_span requires start_ms/end_ms")
        if not str(span.get("text") or "").strip():
            raise _err(sample_id, f"claim {claim['claim_id']!r} evidence_span.text must be non-empty")
    return sample


def load_dataset(path: str | Path) -> list[dict]:
    """加载 JSONL 标注集并逐条校验，返回样本列表。"""
    samples: list[dict] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                sample = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AnnotationError(f"line {line_no}: invalid JSON: {exc}") from exc
            try:
                validate_sample(sample)
            except AnnotationError as exc:
                raise AnnotationError(f"line {line_no}: {exc}") from exc
            samples.append(sample)
    if not samples:
        raise AnnotationError(f"dataset {path} is empty")
    return samples


__all__ = [
    "AnnotationError",
    "VIDEO_TYPES",
    "TRUTH_STATUSES",
    "SUPPORT_LABELS",
    "validate_sample",
    "load_dataset",
]
