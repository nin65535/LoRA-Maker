$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repositoryRoot

if (-not (Test-Path -LiteralPath "$repositoryRoot\frontend\dist\index.html")) {
    throw 'frontend/dist がありません。先に frontend で npm run build を実行してください。'
}

& "$repositoryRoot\.venv\Scripts\python.exe" -m backend.run
