"""P1-5：离线回填 knowledge_unit.source_reliability_score（设计文档 §56-57）。

用法：
    python -m scripts.backfill_source_reliability                # 全量按作者维度回填
    python -m scripts.backfill_source_reliability --author <id>  # 只计算并打印单个来源

注意：作者可靠性只是检索排序弱信号，不能替代单条 Evidence Verification；
本脚本刻意不接入主链路，需人工/调度离线触发。
"""

from __future__ import annotations

import argparse
import json

from engines.content.source_reliability_service import SourceReliabilityService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill knowledge_unit.source_reliability_score per author.")
    parser.add_argument("--author", type=str, default=None, help="只计算指定 author_id / author_name，不写库")
    parser.add_argument("--min-forecast-sample", type=int, default=5)
    args = parser.parse_args(argv)

    service = SourceReliabilityService(min_forecast_sample=args.min_forecast_sample)
    if args.author:
        print(json.dumps(service.compute(args.author), ensure_ascii=False, indent=2))
        return 0
    result = service.backfill()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
