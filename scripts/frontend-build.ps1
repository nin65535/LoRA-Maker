$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $repositoryRoot 'frontend'

Push-Location $frontendRoot
try {
    Write-Host 'Frontend build を開始します...'
    npm run build
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend build に失敗しました。(終了コード: $LASTEXITCODE)"
    }
    Write-Host 'Frontend build が完了しました。' -ForegroundColor Green
}
finally {
    Pop-Location
}
