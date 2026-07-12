param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

. "$PSScriptRoot\project-env.ps1"
Set-ProjectRuntimeEnv

$args = @("-m", "pytest")
if ($PytestArgs) {
    $args += $PytestArgs
}

& (Get-ProjectPython) @args

