# Existing daily task entrypoint; same schedule, native budgets and shared kernel lock.
param([switch]$PreflightOnly)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path (Split-Path -Parent $repo) 'sku-service\.venv-audit\Scripts\python.exe'
$bin = 'D:\tools\pg17-runtime\bin'
$env:PGCONNECT_TIMEOUT = '5'
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    & (Join-Path $bin 'pg_isready.exe') -h 127.0.0.1 -p 55461 | Out-Null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 10
}
if (-not $ready) { exit 50 }
Push-Location $repo
try {
    if ($PreflightOnly) {
        & $python 'scripts/rc_runtime.py' --competitor-preflight
    } else {
        & $python 'scripts/rc_runtime.py' --scheduled-competitors
    }
    $result = $LASTEXITCODE
} finally { Pop-Location }
exit $result
