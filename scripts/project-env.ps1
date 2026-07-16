Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:ProjectRoot = Split-Path -Parent $PSScriptRoot
$script:CondaRoot = "D:\anaconda"
$script:CondaEnvPath = Join-Path $script:ProjectRoot ".conda-env"
$script:PythonExe = Join-Path $script:CondaEnvPath "python.exe"
$script:ScriptsDir = Join-Path $script:CondaEnvPath "Scripts"
$script:LibraryBin = Join-Path $script:CondaEnvPath "Library\bin"
$script:NvidiaSitePackages = Join-Path $script:CondaEnvPath "Lib\site-packages\nvidia"
$script:TesseractExe = Join-Path $script:LibraryBin "tesseract.exe"
$script:TessdataDir = Join-Path $script:CondaEnvPath "share\tessdata"
$script:DotEnvPath = Join-Path $script:ProjectRoot ".env"
$script:DefaultBilibiliCookieFile = Join-Path $script:ProjectRoot "storage\runtime\bilibili.cookies.txt"

function Get-ProjectRoot {
    return $script:ProjectRoot
}

function Get-ProjectPython {
    if (-not (Test-Path $script:PythonExe)) {
        throw "Project conda environment not found: $($script:CondaEnvPath)"
    }
    return $script:PythonExe
}

function Get-BilibiliCookieFilePath {
    return $script:DefaultBilibiliCookieFile
}

function Set-ProjectRuntimeEnv {
    $env:PATH = "$script:ScriptsDir;$script:LibraryBin;$env:PATH"
    if (Test-Path $script:NvidiaSitePackages) {
        $nvidiaBinCandidates = @(
            (Join-Path $script:NvidiaSitePackages "cu12\bin"),
            (Join-Path $script:NvidiaSitePackages "cu12\bin\x86_64"),
            (Join-Path $script:NvidiaSitePackages "cu13\bin"),
            (Join-Path $script:NvidiaSitePackages "cu13\bin\x86_64"),
            (Join-Path $script:NvidiaSitePackages "cuda_runtime\bin"),
            (Join-Path $script:NvidiaSitePackages "cuda_runtime\bin\x86_64"),
            (Join-Path $script:NvidiaSitePackages "cuda_nvrtc\bin"),
            (Join-Path $script:NvidiaSitePackages "cuda_nvrtc\bin\x86_64"),
            (Join-Path $script:NvidiaSitePackages "cublas\bin"),
            (Join-Path $script:NvidiaSitePackages "cublas\bin\x86_64"),
            (Join-Path $script:NvidiaSitePackages "cudnn\bin")
        )
        foreach ($binDir in $nvidiaBinCandidates) {
            if ((Test-Path $binDir) -and (-not (($env:PATH -split ';') -contains $binDir))) {
                $env:PATH = "$binDir;$env:PATH"
            }
        }
    }
    $env:FFMPEG_BIN = Join-Path $script:LibraryBin "ffmpeg.exe"
    $env:FFPROBE_BIN = Join-Path $script:LibraryBin "ffprobe.exe"
    $env:YT_DLP_BIN = Join-Path $script:ScriptsDir "yt-dlp.exe"
    if ((-not $env:TESSERACT_BIN) -and (Test-Path $script:TesseractExe)) {
        $env:TESSERACT_BIN = $script:TesseractExe
    }
    if ((-not $env:TESSDATA_PREFIX) -and (Test-Path $script:TessdataDir)) {
        $env:TESSDATA_PREFIX = $script:TessdataDir
    }
    $env:CONTENT_STORAGE_DIR = "storage/content"
    if (-not $env:BILIBILI_COOKIE_FILE) {
        $env:BILIBILI_COOKIE_FILE = $script:DefaultBilibiliCookieFile
    }
    if (-not $env:ASR_DEVICE) {
        $env:ASR_DEVICE = "auto"
    }
    if (-not $env:ASR_COMPUTE_TYPE) {
        $env:ASR_COMPUTE_TYPE = "auto"
    }
    if (-not $env:ASR_USE_BATCHED) {
        $env:ASR_USE_BATCHED = "false"
    }
    if (-not $env:ASR_BATCH_SIZE) {
        $env:ASR_BATCH_SIZE = "1"
    }
    if (-not $env:ASR_CHUNK_LENGTH_SECONDS) {
        $env:ASR_CHUNK_LENGTH_SECONDS = "30"
    }
    if (-not $env:ASR_BEAM_SIZE) {
        $env:ASR_BEAM_SIZE = "5"
    }
    if (-not $env:ASR_BEST_OF) {
        $env:ASR_BEST_OF = "5"
    }
    if (-not $env:ASR_CONDITION_ON_PREVIOUS_TEXT) {
        $env:ASR_CONDITION_ON_PREVIOUS_TEXT = "true"
    }

    $env:DATABASE_URL = "sqlite:///./financial_agent.db"
    $env:QDRANT_URL = "http://127.0.0.1:6333"
    $env:REDIS_URL = "redis://127.0.0.1:6379/0"
    $env:RERANKER_URL = "http://127.0.0.1:8010"

    if (Test-Path $script:DotEnvPath) {
        foreach ($line in Get-Content $script:DotEnvPath) {
            $trimmed = $line.Trim()
            if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
                continue
            }
            $parts = $trimmed.Split("=", 2)
            $key = $parts[0].Trim()
            $value = $parts[1]
            if ($key -in @(
                "AGENT_MODEL_PROVIDER",
                "AGENT_MODEL_NAME",
                "AGENT_MODEL_BASE_URL",
                "AGENT_MODEL_API_KEY",
                "ANALYSIS_MODEL_PROVIDER",
                "ANALYSIS_MODEL_NAME",
                "ANALYSIS_MODEL_BASE_URL",
                "ANALYSIS_MODEL_API_KEY",
                "VISUAL_MODEL_PROVIDER",
                "VISUAL_MODEL_NAME",
                "VISUAL_MODEL_BASE_URL",
                "VISUAL_MODEL_API_KEY",
                "BILIBILI_COOKIE_FILE",
                "BILIBILI_COOKIE_HEADER",
                "BILIBILI_COOKIES_FROM_BROWSER",
                "BILIBILI_COOKIES_BROWSER_PROFILE",
                "TESSERACT_BIN",
                "VIDEO_OCR_BACKEND",
                "VIDEO_OCR_LANGUAGE",
                "VIDEO_OCR_PADDLE_LANG",
                "VIDEO_OCR_DEVICE",
                "VIDEO_OCR_SCORE_THRESH",
                "VIDEO_OCR_DET_MODEL_NAME",
                "VIDEO_OCR_REC_MODEL_NAME",
                "VIDEO_FRAME_INTERVAL_SECONDS",
                "VIDEO_MAX_FRAMES",
                "VIDEO_VISUAL_CUE_WINDOW_SECONDS",
                "VIDEO_VISUAL_TRANSCRIPT_WINDOW_SECONDS",
                "VIDEO_VISUAL_MAX_CONTEXT_ITEMS",
                "VIDEO_VISION_MAX_FRAMES",
                "ASR_MODEL_SIZE",
                "ASR_DEVICE",
                "ASR_COMPUTE_TYPE",
                "ASR_USE_BATCHED",
                "ASR_BATCH_SIZE",
                "ASR_CHUNK_LENGTH_SECONDS",
                "ASR_BEAM_SIZE",
                "ASR_BEST_OF",
                "ASR_CONDITION_ON_PREVIOUS_TEXT",
                "LOG_LEVEL"
            )) {
                Set-Item -Path "Env:$key" -Value $value
            }
        }
    }
}

function Invoke-ProjectPython {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Args
    )

    Set-ProjectRuntimeEnv
    & (Get-ProjectPython) @Args
}

function New-ProjectCondaEnv {
    if (Test-Path $script:PythonExe) {
        Write-Host "Project conda environment already exists at $script:CondaEnvPath"
        return
    }
    $condaExe = Join-Path $script:CondaRoot "Scripts\conda.exe"
    if (-not (Test-Path $condaExe)) {
        throw "Conda executable not found at $condaExe"
    }
    & $condaExe create -p $script:CondaEnvPath python=3.11 -y
    & (Join-Path $script:CondaEnvPath "python.exe") -m pip install -e ".[dev]"
    & (Join-Path $script:CondaEnvPath "python.exe") -m pip install `
        nvidia-cublas-cu12==12.9.2.10 `
        nvidia-cudnn-cu12==9.24.0.43 `
        nvidia-cuda-runtime-cu12==12.9.79 `
        nvidia-cuda-nvrtc-cu12==12.9.86 `
        -i https://pypi.tuna.tsinghua.edu.cn/simple
    & (Join-Path $script:CondaEnvPath "python.exe") -m pip install paddlepaddle-gpu==3.3.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu130/
    & $condaExe install -p $script:CondaEnvPath -c conda-forge ffmpeg -y
}
