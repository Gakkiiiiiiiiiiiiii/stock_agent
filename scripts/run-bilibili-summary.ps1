param(
    [Parameter(Mandatory = $true)]
    [string]$Url,
    [string]$AsrModel = "medium",
    [ValidateSet("fast", "balanced", "quality", "accurate")]
    [string]$AsrProfile = "accurate",
    [switch]$UseDiarization,
    [switch]$ForceReprocess
)

. "$PSScriptRoot\project-env.ps1"
Set-ProjectRuntimeEnv
$env:ASR_MODEL_SIZE = $AsrModel
$env:ASR_DEVICE = "auto"
$env:ASR_COMPUTE_TYPE = "auto"
switch ($AsrProfile) {
    "fast" {
        $env:ASR_USE_BATCHED = "true"
        $env:ASR_BATCH_SIZE = "16"
        $env:ASR_CHUNK_LENGTH_SECONDS = "30"
        $env:ASR_BEAM_SIZE = "1"
        $env:ASR_BEST_OF = "1"
        $env:ASR_CONDITION_ON_PREVIOUS_TEXT = "false"
    }
    "balanced" {
        $env:ASR_USE_BATCHED = "true"
        $env:ASR_BATCH_SIZE = "12"
        $env:ASR_CHUNK_LENGTH_SECONDS = "30"
        $env:ASR_BEAM_SIZE = "3"
        $env:ASR_BEST_OF = "3"
        $env:ASR_CONDITION_ON_PREVIOUS_TEXT = "false"
    }
    "quality" {
        $env:ASR_USE_BATCHED = "false"
        $env:ASR_BATCH_SIZE = "1"
        $env:ASR_CHUNK_LENGTH_SECONDS = "30"
        $env:ASR_BEAM_SIZE = "5"
        $env:ASR_BEST_OF = "5"
        $env:ASR_CONDITION_ON_PREVIOUS_TEXT = "true"
    }
    "accurate" {
        $env:ASR_USE_BATCHED = "false"
        $env:ASR_BATCH_SIZE = "1"
        $env:ASR_CHUNK_LENGTH_SECONDS = "30"
        $env:ASR_BEAM_SIZE = "5"
        $env:ASR_BEST_OF = "5"
        $env:ASR_CONDITION_ON_PREVIOUS_TEXT = "true"
    }
}

@'
import json
import sys

from engines.content.video_ingest_service import VideoIngestService
from storage.bootstrap import create_all

url = sys.argv[1]
force_reprocess = sys.argv[2].lower() == "true"
use_diarization = sys.argv[3].lower() == "true"

create_all()
service = VideoIngestService()
queued = service.enqueue_bilibili(
    url=url,
    force_reprocess=force_reprocess,
    summary_mode="investment",
    index_to_memory=True,
    use_diarization=use_diarization,
    language_hint="zh",
)

if queued.get("task_id") is None:
    detail = service.get_video_detail(queued["video_id"], summary_mode="investment")
    print(json.dumps({"task": queued, **(detail or {})}, ensure_ascii=False, indent=2))
else:
    detail = service.process_task(queued["task_id"])
    print(json.dumps(detail, ensure_ascii=False, indent=2))
'@ | & (Get-ProjectPython) "-" $Url "$($ForceReprocess.IsPresent)" "$($UseDiarization.IsPresent)"
