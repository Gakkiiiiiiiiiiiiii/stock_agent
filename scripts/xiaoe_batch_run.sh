#!/usr/bin/env bash
# 小鹅通专栏分批抓取+解析驱动：每批 8 节，直到全部完成。
# 用法: bash scripts/xiaoe_batch_run.sh  （建议以分离进程启动，日志见 storage/runtime/batch_run.log）
set -u
cd "$(dirname "$0")/.." || exit 1

SKIP_ID="v_6a69e364e4b0694c5bf159d1"  # 2026-07-29 已解析
LOG="storage/runtime/batch_run.log"
OFFSETS="0 8 16 24 32 40 48 56"

for offset in $OFFSETS; do
  echo "[$(date '+%F %T')] === batch offset=$offset capture start ===" >> "$LOG"
  if ! .venv/Scripts/python.exe scripts/xiaoe_batch_capture.py --offset "$offset" --limit 8 >> "$LOG" 2>&1; then
    echo "[$(date '+%F %T')] capture FAILED offset=$offset, skip batch" >> "$LOG"
    continue
  fi
  ids=$(.venv/Scripts/python.exe -c "
import json
rows = json.load(open('storage/runtime/xiaoe_batch_cache/play_urls.private.json', encoding='utf-8'))
print(' '.join(str(r.get('resource_id')) for r in rows if r.get('resource_id') and r.get('resource_id') != '$SKIP_ID'))
")
  for rid in $ids; do
    echo "[$(date '+%F %T')] parse start $rid" >> "$LOG"
    if docker exec stock-agent-api python scripts/xiaoe_parse_cached_video.py --resource-id "$rid" --enable-visual --index-knowledge > "storage/runtime/batch_parse_${rid}.log" 2>&1; then
      echo "[$(date '+%F %T')] parse OK $rid" >> "$LOG"
    else
      echo "[$(date '+%F %T')] parse FAIL $rid (见 batch_parse_${rid}.log)" >> "$LOG"
    fi
  done
done
echo "[$(date '+%F %T')] ALL BATCHES DONE" >> "$LOG"
