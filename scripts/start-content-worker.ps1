. "$PSScriptRoot\project-env.ps1"
Set-ProjectRuntimeEnv
& (Get-ProjectPython) "-m" "workers.content_ingest_worker"

