param(
    [string]$Host = "127.0.0.1",
    [int]$Port = 8000,
    [switch]$Reload
)

. "$PSScriptRoot\project-env.ps1"
Set-ProjectRuntimeEnv

$args = @("-m", "uvicorn", "app.api:app", "--host", $Host, "--port", "$Port")
if ($Reload) {
    $args += "--reload"
}

& (Get-ProjectPython) @args

