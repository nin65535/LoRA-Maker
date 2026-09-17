[CmdletBinding()]
param(
    [string]$StabilityMatrixRoot = 'G:\StabilityMatrix',
    [string]$ComfyUiApiUrl = 'http://127.0.0.1:8188',
    [string]$BandiViewPath = 'C:\Program Files\BandiView\BandiView.exe'
)

$ErrorActionPreference = 'Stop'

function Write-Check {
    param([string]$Name, [bool]$Passed, [string]$Detail)
    $status = if ($Passed) { 'PASS' } else { 'FAIL' }
    [pscustomobject]@{ Status = $status; Check = $Name; Detail = $Detail }
}

function Get-CommandVersion {
    param([string]$Name, [string]$ExpectedVersion)
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    $expectation = if ($ExpectedVersion) { "expected $ExpectedVersion" } else { 'must be available' }
    if (-not $command) {
        return Write-Check $Name $false "not found; $expectation"
    }
    $actual = (& $command.Source --version 2>&1 | Select-Object -First 1).ToString()
    $passed = -not $ExpectedVersion -or $actual -match [regex]::Escape($ExpectedVersion)
    Write-Check $Name $passed "$actual ($($command.Source)); $expectation"
}

$packageRoot = Join-Path $StabilityMatrixRoot 'Data\Packages'
$comfyRoot = Join-Path $packageRoot 'ComfyUI'
$kohyaRoot = Join-Path $packageRoot 'kohya_ss'
$kohyaPython = Join-Path $kohyaRoot 'venv\Scripts\python.exe'
$trainNetwork = Join-Path $kohyaRoot 'sd-scripts\train_network.py'
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$nodeVersion = (Get-Content -LiteralPath (Join-Path $projectRoot '.node-version') -Raw).Trim()
$fnmNodeRoot = Join-Path $env:APPDATA "fnm\node-versions\v$nodeVersion\installation"
$nodeExecutable = Join-Path $fnmNodeRoot 'node.exe'
$npmExecutable = Join-Path $fnmNodeRoot 'npm.cmd'

$projectPythonCheck = if (Test-Path -LiteralPath $projectPython) {
    $actual = (& $projectPython -V 2>&1 | Select-Object -First 1).ToString()
    Write-Check 'project Python' ($actual -match '3\.14\.7') "$actual ($projectPython); expected 3.14.7"
}
else {
    Write-Check 'project Python' $false "$projectPython not found; expected 3.14.7"
}

$pythonPackagesCheck = if (Test-Path -LiteralPath $projectPython) {
    $actual = (& $projectPython -c "import fastapi, pydantic; print(f'FastAPI {fastapi.__version__}, Pydantic {pydantic.__version__}')" 2>&1 | Select-Object -Last 1).ToString()
    Write-Check 'Python packages' ($LASTEXITCODE -eq 0 -and $actual -eq 'FastAPI 0.141.1, Pydantic 2.13.5') "$actual; expected FastAPI 0.141.1, Pydantic 2.13.5"
}
else {
    Write-Check 'Python packages' $false 'project Python is unavailable'
}

$nodeCheck = if (Test-Path -LiteralPath $nodeExecutable) {
    $actual = (& $nodeExecutable -v 2>&1 | Select-Object -First 1).ToString()
    Write-Check 'node' ($actual -eq "v$nodeVersion") "$actual ($nodeExecutable); expected v$nodeVersion"
}
else {
    Get-CommandVersion 'node' $nodeVersion
}

$npmCheck = if (Test-Path -LiteralPath $npmExecutable) {
    $actual = (& $npmExecutable -v 2>&1 | Select-Object -First 1).ToString()
    Write-Check 'npm' ($actual -eq '11.19.1') "$actual ($npmExecutable); expected 11.19.1"
}
else {
    Get-CommandVersion 'npm' '11.19.1'
}

$checks = @(
    $projectPythonCheck
    $pythonPackagesCheck
    $nodeCheck
    $npmCheck
    Get-CommandVersion 'ffmpeg' ''
    Write-Check 'Stability Matrix' (Test-Path -LiteralPath (Join-Path $StabilityMatrixRoot 'StabilityMatrix.exe')) $StabilityMatrixRoot
    Write-Check 'ComfyUI package' (Test-Path -LiteralPath (Join-Path $comfyRoot 'main.py')) $comfyRoot
    Write-Check 'kohya_ss Python' (Test-Path -LiteralPath $kohyaPython) $kohyaPython
    Write-Check 'sd-scripts train_network.py' (Test-Path -LiteralPath $trainNetwork) $trainNetwork
    Write-Check 'BandiView' (Test-Path -LiteralPath $BandiViewPath) $BandiViewPath
)

try {
    $null = Invoke-RestMethod -Uri "$($ComfyUiApiUrl.TrimEnd('/'))/system_stats" -TimeoutSec 3
    $checks += Write-Check 'ComfyUI API' $true "$ComfyUiApiUrl (system_stats returned)"
}
catch {
    $checks += Write-Check 'ComfyUI API' $false "$ComfyUiApiUrl ($($_.Exception.Message))"
}

$checks | Format-Table -AutoSize -Wrap
if ($checks.Status -contains 'FAIL') { exit 1 }
