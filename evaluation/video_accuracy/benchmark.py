"""P2-2 Accuracy CI Gate（设计文档 §74/§86）：Golden Dataset 准确率基准与门禁。

输入：
- golden dataset：evaluation/video_accuracy/sample_annotations.jsonl 格式（schema.py 校验）；
- 系统产物导出 JSON：{"videos": [{"video_id": ..., "units": [<KnowledgeUnit 序列化 dict>]}]}，
  即指定视频在知识库中的 KnowledgeUnit 导出（statement / entities / support_status /
  support_score / truth_status / speaker_id 等字段）。

输出：report JSON（metrics + 逐项 gate 结果）。默认启用 §86 门禁，任一不达标 exit 1，
供 CI 使用；--no-gate 只出报告不卡退出码。

指标口径（启发式 scaffold，真实门禁前需用 50-100 视频人工标注集校准）：
- claim 与系统 unit 的匹配按字符 bigram 包含度 >= 0.5；
- 关键数字用 engines/content/financial_numeric 解析后按区间/单位语义比对；
- 否定检测为关键词近似（不/没/无/未/并非/否认）；
- Calibration ECE 仅在 (support_score, label) 对数 >= --ece-min-pairs 时纳入门禁，
  否则报告中标记 skipped（§75：校准通过前 support_score 不得解释为概率）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from engines.content.financial_numeric import FinancialNumericValue, numeric_values_match, parse_financial_numerics
from engines.content.knowledge_enums import SupportStatus, support_rank
from evaluation.video_accuracy.calibration import brier_score, expected_calibration_error, reliability_bins
from evaluation.video_accuracy.schema import load_dataset

# §86 Golden Dataset 验收指标门禁。
GATES: dict[str, tuple[str, float]] = {
    "entity_accuracy": ("gte", 0.98),
    "ticker_exact_match": ("gte", 0.995),
    "numeric_exact_match": ("gte", 0.99),
    "negation_accuracy": ("gte", 0.995),
    "claim_precision": ("gte", 0.98),
    "source_supported_precision": ("gte", 0.99),
    "unsupported_claim_rate": ("lt", 0.01),
    "critical_numeric_hallucination": ("eq", 0.0),
    "critical_speaker_attribution_error": ("eq", 0.0),
}

_MATCH_THRESHOLD = 0.5
_NEGATION_WORDS = ("并非", "不再", "否认", "不", "没", "无", "未")


def load_system_export(path: str | Path) -> dict[str, list[dict]]:
    """加载系统产物导出 JSON，返回 {video_id: [unit dict]}。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    videos = payload.get("videos") if isinstance(payload, dict) else None
    if not isinstance(videos, list):
        raise ValueError("system export must be an object with a 'videos' list")
    result: dict[str, list[dict]] = {}
    for video in videos:
        video_id = str(video.get("video_id") or "")
        if not video_id:
            raise ValueError("each video entry requires video_id")
        result[video_id] = list(video.get("units") or [])
    return result


def _bigrams(text: str) -> set[str]:
    text = "".join(str(text).split())
    if len(text) < 2:
        return {text} if text else set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def _containment(golden: str, candidate: str) -> float:
    golden_bg = _bigrams(golden)
    if not golden_bg:
        return 0.0
    return len(golden_bg & _bigrams(candidate)) / len(golden_bg)


def _best_matching_unit(claim_statement: str, units: list[dict]) -> dict | None:
    best: dict | None = None
    best_score = 0.0
    for unit in units:
        for field in ("canonical_statement", "statement"):
            score = _containment(claim_statement, str(unit.get(field) or ""))
            if score > best_score:
                best, best_score = unit, score
    return best if best_score >= _MATCH_THRESHOLD else None


def _has_negation(text: str) -> bool:
    return any(word in text for word in _NEGATION_WORDS)


def _golden_numeric(number: dict) -> FinancialNumericValue:
    return FinancialNumericValue(
        raw_expression=str(number.get("raw_expression") or ""),
        value=number.get("value"),
        min_value=number.get("min_value"),
        max_value=number.get("max_value"),
        comparator=number.get("comparator"),
        approximate=bool(number.get("approximate")),
        unit=number.get("unit"),
        metric=number.get("metric"),
    )


def compute_metrics(samples: list[dict], system_by_video: dict[str, list[dict]]) -> dict:
    """对 golden samples 与系统 unit 导出计算 §86 指标。值 None 表示分母为 0（跳过门禁）。"""
    entity_total = entity_hit = 0
    ticker_total = ticker_hit = 0
    numeric_total = numeric_hit = 0
    negation_total = negation_hit = 0
    unit_total = unit_matched = 0
    supported_total = supported_hit = 0
    unsupported_units = 0
    critical_numeric_hallucination = 0
    critical_speaker_attribution_error = 0
    calibration_pairs: list[tuple[float, int]] = []

    for sample in samples:
        units = system_by_video.get(str(sample["video_id"]), [])
        system_names = {
            str(entity.get("entity_name") or "")
            for unit in units
            for entity in (unit.get("entities") or [])
        } | {str(unit.get("subject_name") or "") for unit in units}
        system_tickers = {
            str(entity.get("ticker") or "")
            for unit in units
            for entity in (unit.get("entities") or [])
            if entity.get("ticker")
        } | {str(unit.get("subject_key") or "") for unit in units if str(unit.get("subject_key") or "").isdigit()}
        system_names.discard("")

        parsed_numerics = [
            value
            for unit in units
            for field in ("canonical_statement", "statement")
            for value in parse_financial_numerics(str(unit.get(field) or ""))
        ]

        for entity in sample["entities"]:
            entity_total += 1
            if str(entity.get("name") or "") in system_names or (
                entity.get("ticker") and str(entity["ticker"]) in system_tickers
            ):
                entity_hit += 1
            if entity.get("ticker"):
                ticker_total += 1
                if str(entity["ticker"]) in system_tickers:
                    ticker_hit += 1

        for number in sample["numbers"]:
            numeric_total += 1
            golden_value = _golden_numeric(number)
            if any(numeric_values_match(golden_value, candidate) for candidate in parsed_numerics):
                numeric_hit += 1
            elif any(
                candidate.unit and golden_value.unit and candidate.unit == golden_value.unit
                and candidate.metric and golden_value.metric and candidate.metric == golden_value.metric
                for candidate in parsed_numerics
            ):
                # 同指标同单位但数值对不上：Critical Numeric Hallucination（§86 要求为 0）。
                critical_numeric_hallucination += 1

        matched_unit_by_claim: dict[str, dict | None] = {}
        for claim in sample["claims"]:
            matched = _best_matching_unit(str(claim["statement"]), units)
            matched_unit_by_claim[str(claim["claim_id"])] = matched
            if claim.get("negation") is None or matched is None:
                continue
            negation_total += 1
            if _has_negation(str(matched.get("statement") or "")) == claim["negation"]:
                negation_hit += 1
            if claim.get("speaker_id") and matched.get("speaker_id"):
                if str(matched["speaker_id"]) != str(claim["speaker_id"]):
                    critical_speaker_attribution_error += 1

        golden_supported_ids = {
            str(claim["claim_id"]) for claim in sample["claims"] if claim["support_label"] == "SUPPORTED"
        }
        matched_claim_ids = {claim_id for claim_id, unit in matched_unit_by_claim.items() if unit is not None}
        for unit in units:
            unit_total += 1
            rank = support_rank(unit.get("support_status"))
            if rank == 0:
                unsupported_units += 1
            unit_matched_to = [
                claim_id for claim_id, matched in matched_unit_by_claim.items() if matched is unit
            ]
            is_matched = bool(unit_matched_to)
            is_supported_match = bool(set(unit_matched_to) & golden_supported_ids)
            if is_matched:
                unit_matched += 1
            if rank >= support_rank(SupportStatus.SOURCE_SUPPORTED.value):
                supported_total += 1
                if is_supported_match:
                    supported_hit += 1
            if unit.get("support_score") is not None:
                calibration_pairs.append((float(unit["support_score"]), 1 if is_supported_match else 0))

    def _ratio(hit: int, total: int) -> float | None:
        return (hit / total) if total else None

    metrics = {
        "entity_accuracy": _ratio(entity_hit, entity_total),
        "ticker_exact_match": _ratio(ticker_hit, ticker_total),
        "numeric_exact_match": _ratio(numeric_hit, numeric_total),
        "negation_accuracy": _ratio(negation_hit, negation_total),
        "claim_precision": _ratio(unit_matched, unit_total),
        "source_supported_precision": _ratio(supported_hit, supported_total),
        "unsupported_claim_rate": _ratio(unsupported_units, unit_total),
        "critical_numeric_hallucination": float(critical_numeric_hallucination),
        "critical_speaker_attribution_error": float(critical_speaker_attribution_error),
        "counts": {
            "samples": len(samples),
            "entities": entity_total,
            "tickers": ticker_total,
            "numbers": numeric_total,
            "negation_claims": negation_total,
            "system_units": unit_total,
        },
    }
    metrics["calibration"] = {
        "pairs": len(calibration_pairs),
        "ece": expected_calibration_error(calibration_pairs) if calibration_pairs else None,
        "brier": brier_score(calibration_pairs) if calibration_pairs else None,
        "bins": reliability_bins(calibration_pairs) if calibration_pairs else [],
    }
    return metrics


def apply_gates(metrics: dict, *, ece_min_pairs: int = 20) -> dict:
    """按 §86 阈值逐项判定，返回 {"metric": {...}, "passed": bool}。"""
    results: dict[str, dict] = {}
    passed = True
    for metric, (op, threshold) in GATES.items():
        value = metrics.get(metric)
        if value is None:
            results[metric] = {"value": None, "threshold": threshold, "status": "skipped"}
            continue
        ok = {"gte": value >= threshold, "lt": value < threshold, "eq": value == threshold}[op]
        results[metric] = {"value": value, "threshold": f"{op} {threshold}", "status": "pass" if ok else "fail"}
        passed = passed and ok
    calibration = metrics.get("calibration") or {}
    ece = calibration.get("ece")
    if ece is None or calibration.get("pairs", 0) < ece_min_pairs:
        results["calibration_ece"] = {
            "value": ece,
            "threshold": "lte 0.05",
            "status": "skipped",
            "note": f"calibration pairs < {ece_min_pairs}，ECE 不纳入门禁",
        }
    else:
        ok = ece <= 0.05
        results["calibration_ece"] = {"value": ece, "threshold": "lte 0.05", "status": "pass" if ok else "fail"}
        passed = passed and ok
    return {"checks": results, "passed": passed}


def evaluate(dataset_path: str | Path, system_path: str | Path, *, ece_min_pairs: int = 20) -> dict:
    samples = load_dataset(dataset_path)
    system_by_video = load_system_export(system_path)
    metrics = compute_metrics(samples, system_by_video)
    gate = apply_gates(metrics, ece_min_pairs=ece_min_pairs)
    return {
        "dataset": str(dataset_path),
        "system_export": str(system_path),
        "metrics": metrics,
        "gate": gate,
        "passed": gate["passed"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Golden video accuracy benchmark + §86 gate.")
    parser.add_argument("--dataset", type=Path, required=True, help="golden dataset JSONL")
    parser.add_argument("--system", type=Path, required=True, help="系统 KnowledgeUnit 导出 JSON")
    parser.add_argument("--output", type=Path, default=None, help="report JSON 输出路径")
    parser.add_argument("--no-gate", action="store_true", help="只出报告，不卡退出码")
    parser.add_argument("--ece-min-pairs", type=int, default=20, help="ECE 纳入门禁的最小 (score,label) 对数")
    args = parser.parse_args(argv)

    report = evaluate(args.dataset, args.system, ece_min_pairs=args.ece_min_pairs)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for metric, check in report["gate"]["checks"].items():
        print(f"[{check['status']:>7}] {metric}: {check.get('value')} (gate: {check.get('threshold')})")
    print(f"overall: {'PASS' if report['passed'] else 'FAIL'}")
    if args.no_gate or report["passed"]:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
