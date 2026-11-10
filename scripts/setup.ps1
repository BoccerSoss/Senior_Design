param([string]$PythonPath = 'py')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if ($PythonPath -eq 'py') { & py -3.12 -m venv .venv }
    else { & $PythonPath -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw 'Creating Python 3.12 environment failed.' }
}
& .\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'Installing pinned dependencies failed.' }
& .\.venv\Scripts\python.exe scripts/aip.py doctor
if ($LASTEXITCODE -ne 0) { throw 'Environment check failed.' }
