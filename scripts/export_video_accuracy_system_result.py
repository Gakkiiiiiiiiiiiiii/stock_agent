"""导出真实 Video Pipeline 的 KnowledgeUnit，供 video accuracy benchmark 评测（§11）。

输入为 video_id 列表（KnowledgeRepository 的整数 video_id），输出：

```json
{"videos": [{"video_id": "...", "units": [<KnowledgeUnit 序列化 dict>]}]}
```

unit 字段直接沿用 ``KnowledgeRepository.list_units_for_video`` 的序列化结果
（statement / canonical_statement / entities / support_status / support_score /
truth_status / speaker_id 等），即 evaluation/video_accuracy/benchmark.py
``load_system_export`` 的消费契约。

用法：

```bash
# 1) 导出真实系统结果（video_id 为知识库整数 ID；也可用 --video-ids-file 逐行读取）
python -m scripts.export_video_accuracy_system_result \
  --video-ids 101 102 103 \
  --output artifacts/video_accuracy/real_system_export.json

# 2) 跑真实 benchmark gate（§14）
python -m evaluation.video_accuracy.benchmark \
  --dataset evaluation/video_accuracy/golden_annotations.jsonl \
  --system artifacts/video_accuracy/real_system_export.json \
  --output artifacts/video_accuracy/real_report.json
```

数据库连接沿用 storage.db（DATABASE_URL 环境变量，默认 sqlite:///./financial_agent.db）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from storage.repositories.knowledge_repository import KnowledgeRepository

DEFAULT_OUTPUT = Path("artifacts/video_accuracy/real_system_export.json")


def export_system_result(video_ids: list[str], repository: KnowledgeRepository | None = None) -> dict:
    """按 video_id 列表从 KnowledgeRepository 导出 units，保持 benchmark 消费契约。"""
    repo = repository or KnowledgeRepository()
    videos: list[dict] = []
    for raw_id in video_ids:
        video_id = str(raw_id).strip()
        if not video_id:
            continue
        try:
            db_video_id = int(video_id)
        except ValueError as exc:
            raise ValueError(f"video_id 必须是知识库整数 ID，收到: {raw_id!r}") from exc
        # 输出保留原始字符串形式，benchmark 按 str(video_id) 与 golden dataset 对齐。
        videos.append({"video_id": video_id, "units": repo.list_units_for_video(db_video_id)})
    return {"videos": videos}


def _read_video_ids_file(path: Path) -> list[str]:
    ids: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            ids.append(line)
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出真实 pipeline 的 KnowledgeUnit 供 accuracy benchmark 评测（§11）。")
    parser.add_argument("--video-ids", nargs="*", default=[], help="知识库整数 video_id 列表")
    parser.add_argument("--video-ids-file", type=Path, default=None, help="逐行 video_id 文件（# 开头为注释）")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help=f"输出 JSON 路径（默认 {DEFAULT_OUTPUT}）")
    args = parser.parse_args(argv)

    video_ids = list(args.video_ids)
    if args.video_ids_file:
        video_ids.extend(_read_video_ids_file(args.video_ids_file))
    if not video_ids:
        parser.error("至少提供一个 video_id（--video-ids 或 --video-ids-file）")

    payload = export_system_result(video_ids)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    total_units = sum(len(video["units"]) for video in payload["videos"])
    print(f"exported {len(payload['videos'])} videos, {total_units} units -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
