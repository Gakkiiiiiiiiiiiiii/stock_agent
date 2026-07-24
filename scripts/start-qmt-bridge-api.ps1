param(
    [int]$Port = 18080
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\project-env.ps1"

$envPath = Join-Path (Get-Location) ".env"
if (Test-Path $envPath) {
    foreach ($line in Get-Content $envPath) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }
        $parts = $trimmed.Split("=", 2)
        $key = $parts[0].Trim()
        $value = $parts[1]
        if ($key -in @("QMT_BRIDGE_PYTHON", "QMT_BRIDGE_SCRIPT", "QMT_INSTALL_DIR", "QMT_USERDATA_DIR", "QMT_ACCOUNT_ID")) {
            Set-Item -Path "env:$key" -Value $value
        }
    }
}

$python = Join-Path (Get-Location) ".conda-env\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$logDir = Join-Path (Get-Location) "storage\runtime"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdoutPath = Join-Path $logDir "qmt_bridge_http.out.log"
$stderrPath = Join-Path $logDir "qmt_bridge_http.err.log"

$args = @(
    "scripts/qmt_bridge_http.py",
    "--host", "0.0.0.0",
    "--port", "$Port",
    "--bridge-python", $env:QMT_BRIDGE_PYTHON,
    "--bridge-script", $env:QMT_BRIDGE_SCRIPT,
    "--install-dir", $env:QMT_INSTALL_DIR,
    "--userdata-dir", $env:QMT_USERDATA_DIR
)
if ($env:QMT_ACCOUNT_ID) {
    $args += @("--account-id", $env:QMT_ACCOUNT_ID)
}

Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory (Get-Location) -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -WindowStyle Hidden
Write-Host "QMT HTTP bridge started on http://127.0.0.1:$Port"
Write-Host "Logs: $stdoutPath ; $stderrPath"
