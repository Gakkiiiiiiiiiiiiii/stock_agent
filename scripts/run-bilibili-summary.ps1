param(
    [Parameter(Mandatory = $true)]
    [string]$Url,
    [string]$AsrModel = "medium",
    [ValidateSet("fast", "balanced", "quality", "accurate")]
    [string]$AsrProfile = "accurate",
    [switch]$UseDiarization,
    [switch]$ForceReprocess,
    [ValidateSet("auto", "docker", "local")]
    [string]$Runtime = "auto",
    [string]$ApiBaseUrl = "http://127.0.0.1:8000",
    [int]$PollSeconds = 5,
    [int]$TimeoutMinutes = 180
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

$pythonScript = @'
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
'@

function Get-DockerApiContainerName {
    $containerName = "financial_agent_api"
    try {
        $status = docker inspect -f "{{.State.Running}}" $containerName 2>$null
        if ($LASTEXITCODE -eq 0 -and "$status".Trim() -eq "true") {
            return $containerName
        }
    } catch {
    }
    return $null
}

function Invoke-ApiJson {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("GET", "POST")]
        [string]$Method,
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [object]$Body = $null
    )

    $uri = $ApiBaseUrl.TrimEnd("/") + $Path
    if ($null -eq $Body) {
        return Invoke-RestMethod -Method $Method -Uri $uri
    }
    $jsonBody = $Body | ConvertTo-Json -Depth 8
    return Invoke-RestMethod -Method $Method -Uri $uri -ContentType "application/json" -Body $jsonBody
}

function Write-TaskProgress {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Task
    )
    $stamp = Get-Date -Format "HH:mm:ss"
    $video = if ($null -ne $Task.video_id) { $Task.video_id } else { "-" }
    $errorSuffix = if ($Task.error_message) { " error=$($Task.error_message)" } else { "" }
    Write-Host ("[{0}] task={1} video={2} status={3} stage={4} progress={5}%{6}" -f $stamp, $Task.task_id, $video, $Task.status, $Task.stage, $Task.progress, $errorSuffix)
}

function Invoke-VideoSummaryViaApi {
    Invoke-ApiJson -Method "GET" -Path "/health" | Out-Null

    $payload = @{
        url = $Url
        force_reprocess = $ForceReprocess.IsPresent
        summary_mode = "investment"
        index_to_memory = $true
        use_diarization = $UseDiarization.IsPresent
        language_hint = "zh"
        enable_visual_context = $true
    }
    $queued = Invoke-ApiJson -Method "POST" -Path "/api/v1/content/bilibili/ingest" -Body $payload
    Write-Host ("Queued: task_id={0} video_id={1} status={2} stage={3} deduplicated={4}" -f $queued.task_id, $queued.video_id, $queued.status, $queued.stage, $queued.deduplicated)

    if ($null -eq $queued.task_id) {
        if ($null -ne $queued.video_id) {
            return Invoke-ApiJson -Method "GET" -Path ("/api/v1/content/videos/{0}?summary_mode=investment" -f $queued.video_id)
        }
        return $queued
    }

    $started = Invoke-ApiJson -Method "POST" -Path ("/api/v1/content/tasks/{0}/process" -f $queued.task_id)
    Write-Host ("Processor: started={0}" -f $started.started)

    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    $lastFingerprint = ""
    while ($true) {
        if ((Get-Date) -gt $deadline) {
            throw ("Timed out after {0} minutes waiting for task {1}." -f $TimeoutMinutes, $queued.task_id)
        }

        $task = Invoke-ApiJson -Method "GET" -Path ("/api/v1/content/tasks/{0}" -f $queued.task_id)
        $fingerprint = "{0}|{1}|{2}|{3}|{4}" -f $task.task_id, $task.video_id, $task.status, $task.stage, $task.progress
        if ($fingerprint -ne $lastFingerprint) {
            Write-TaskProgress -Task $task
            $lastFingerprint = $fingerprint
        }

        if ($task.status -eq "success") {
            if ($null -ne $task.video_id) {
                return Invoke-ApiJson -Method "GET" -Path ("/api/v1/content/videos/{0}?summary_mode=investment" -f $task.video_id)
            }
            return @{ task = $task }
        }
        if ($task.status -eq "failed") {
            throw ("Task {0} failed at stage '{1}': {2}" -f $task.task_id, $task.stage, $task.error_message)
        }

        Start-Sleep -Seconds $PollSeconds
    }
}

function Invoke-VideoSummaryLocally {
    $pythonScript | & (Get-ProjectPython) "-" $Url "$($ForceReprocess.IsPresent)" "$($UseDiarization.IsPresent)"
}

$dockerContainer = Get-DockerApiContainerName
$useDocker = $false

if ($Runtime -eq 'docker') {
    if (-not $dockerContainer) {
        throw 'Docker runtime requested, but no running financial_agent_api container was found.'
    }
    $useDocker = $true
} elseif ($Runtime -eq 'local') {
    $useDocker = $false
} else {
    $useDocker = $null -ne $dockerContainer
}

if ($useDocker) {
    Write-Host ('Using Docker API runtime: ' + $dockerContainer + ' via ' + $ApiBaseUrl)
    Write-Host 'Note: -AsrProfile/-AsrModel only apply to local runtime; Docker mode uses ASR_* from docker-compose.yml and .env.'
    $detail = Invoke-VideoSummaryViaApi
    $detail | ConvertTo-Json -Depth 20
} else {
    $projectPython = Get-ProjectPython
    Write-Host ('Using local runtime: ' + $projectPython)
    Invoke-VideoSummaryLocally
}
