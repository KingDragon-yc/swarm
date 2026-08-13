param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CollabArgs
)

$ErrorActionPreference = 'Stop'
$HarnessRoot = Split-Path -Parent $PSScriptRoot
$BundledPython = Join-Path $HarnessRoot '.venv\Scripts\python.exe'

if (Test-Path -LiteralPath $BundledPython) {
    $Python = $BundledPython
} else {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $PythonCommand) {
        throw 'Python was not found. Install Python 3.11+ or run scripts\install.ps1.'
    }
    $Python = $PythonCommand.Source
}

Set-Location $HarnessRoot
& $Python -m collab_demo @CollabArgs
