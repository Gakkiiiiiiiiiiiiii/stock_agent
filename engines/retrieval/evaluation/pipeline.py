"""检索评测的可导入入口（§29 retrieval_evaluation 任务 / scripts/run_retrieval_evaluation.py 复用）。

幂等：同一 (dataset, output_dir, ablation) 重复执行覆盖同一批报告文件。
"""
from __future__ import annotations

from pathlib import Path

from engines.retrieval.evaluation.fixture_corpus import build_fixture_hybrid_retriever, load_fixture_records
from engines.retrieval.evaluation.report import write_evaluation_report
from engines.retrieval.evaluation.runner import RetrievalEvaluationRunner

DEFAULT_DATASET = Path("engines/retrieval/evaluation/datasets/golden_v1.jsonl")
DEFAULT_OUTPUT_DIR = Path("artifacts/retrieval_eval")


def run_fixture_evaluation(
    dataset: str | Path = DEFAULT_DATASET,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    ablation: bool = False,
) -> dict:
    """对版本化 fixture 语料运行确定性 HybridRetriever 评测并落盘报告。"""
    dataset = Path(dataset)
    output_dir = Path(output_dir)
    corpus = load_fixture_records()
    runner = RetrievalEvaluationRunner(build_fixture_hybrid_retriever(), corpus_records=corpus)
    cases = runner.load_dataset(dataset)
    evaluation = runner.run(cases)
    report_path = write_evaluation_report(evaluation, output_dir / "baseline")
    result: dict = {
        "dataset": str(dataset),
        "cases": len(cases),
        "summary": evaluation.get("summary") or {},
        "report": str(report_path),
    }
    if ablation:
        from engines.retrieval.evaluation.ablation_runner import (
            RetrievalAblationRunner,
            build_standard_ablation_variants,
        )

        variants = build_standard_ablation_variants(factory=build_fixture_hybrid_retriever)
        reports = RetrievalAblationRunner(variants, corpus_records=corpus).run(cases, output_dir / "ablation")
        result["ablation_variants"] = [report.variant for report in reports]
    return result
