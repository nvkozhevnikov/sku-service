param(
    [string]$Username = 'operator',
    [switch]$EnableRcWrite
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$bin = 'C:\Users\man503\AppData\Local\Temp\universal_supplier_stage4_pg17_runtime\bin'
$passFile = Join-Path $env:LOCALAPPDATA 'UniversalSupplier\rc-local\pgpass.conf'
if (-not (Test-Path -LiteralPath $passFile)) { throw 'Local RC credentials are missing.' }
if (Get-NetTCPConnection -LocalPort 58097 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 58097 already has a listener. Stop only the known old UI manually.'
}
& (Join-Path $bin 'pg_isready.exe') -h 127.0.0.1 -p 55449 -d universal_supplier_server | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Dedicated RC PostgreSQL 55449 is not ready; this script does not start it.' }
$env:PGPASSFILE = $passFile
$env:LOCAL_RC_UI_CONFIRM = 'YES'
$env:LOCAL_RC_UI_USERNAME = $Username
$securePassword = Read-Host 'Local RC panel password' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try { $env:LOCAL_RC_UI_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
if ([string]::IsNullOrWhiteSpace($env:LOCAL_RC_UI_PASSWORD)) { throw 'An empty panel password is not allowed.' }
$bytes = New-Object byte[] 48
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
$env:LOCAL_RC_UI_SESSION_SECRET = [Convert]::ToBase64String($bytes)
if ($EnableRcWrite) {
    $confirm = Read-Host 'Type WRITE_RC_55449 to enable manually confirmed commercial writes'
    if ($confirm -cne 'WRITE_RC_55449') { throw 'RC write was not confirmed.' }
    $env:COMMERCIAL_SERVER_WRITE_ENABLE = 'YES'
    $env:COMMERCIAL_SERVER_WRITE_CONFIRM = 'universal_supplier_server'
} else {
    Remove-Item Env:\COMMERCIAL_SERVER_WRITE_ENABLE -ErrorAction SilentlyContinue
    Remove-Item Env:\COMMERCIAL_SERVER_WRITE_CONFIRM -ErrorAction SilentlyContinue
}
Set-Location -LiteralPath $root
Write-Host 'Starting the local RC panel at http://127.0.0.1:58097/ (no scheduler or PostgreSQL startup).'
uv run --offline --with-requirements requirements.txt python scripts/run_local_rc_ui.py
if ($LASTEXITCODE -ne 0) { throw "Local RC panel exited with code $LASTEXITCODE" }
