# One-time operator bootstrap for the dedicated, persistent local RC cluster.
# Run from an ordinary user PowerShell. It never touches 55447, 55448 or Docker.
param(
    [switch]$InitializeOnly
)

$ErrorActionPreference = 'Stop'
$bin = 'C:\Users\man503\AppData\Local\Temp\universal_supplier_stage4_pg17_runtime\bin'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$data = Join-Path (Split-Path -Parent $repo) 'universal_supplier_rc_pg17_data'
$log = Join-Path $repo 'reports\RC_LOCAL\postgres.log'
$secretDir = Join-Path $env:LOCALAPPDATA 'UniversalSupplier\rc-local'
$passFile = Join-Path $secretDir 'pgpass.conf'
$port = 55449

if ((& (Join-Path $bin 'postgres.exe') --version) -notmatch '17\.11') {
    throw 'Only the pinned PostgreSQL 17.11 binaries are allowed.'
}
if (Test-Path -LiteralPath $data) {
    throw "Target data directory already exists; refusing to overwrite: $data"
}
if (Test-Path -LiteralPath $passFile) {
    throw "RC credential file already exists; refusing to overwrite: $passFile"
}
$tcp = [System.Net.Sockets.TcpClient]::new()
try {
    $pending = $tcp.BeginConnect('127.0.0.1', $port, $null, $null)
    if ($pending.AsyncWaitHandle.WaitOne(500)) {
        try { $tcp.EndConnect($pending); throw "Port $port is already in use." }
        catch [System.Net.Sockets.SocketException] { }
    }
} finally { $tcp.Dispose() }

Write-Host "Binary: $bin\postgres.exe"
Write-Host "Data: $data"
Write-Host "Port: $port; database to restore later: universal_supplier_server; admin role: rc_admin"
Write-Host 'No Docker, service, scheduler, source-cluster write or production connection will be started.'
$confirm = Read-Host 'Type INIT_RC_55449 to initialize this new cluster'
if ($confirm -cne 'INIT_RC_55449') { throw 'Not confirmed; no cluster initialized.' }

$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$bytes = New-Object byte[] 36
$rng.GetBytes($bytes)
$password = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', 'A').Replace('/', 'B')
$rng.Dispose()
$temporary = [System.IO.Path]::GetTempFileName()
try {
    [System.IO.File]::WriteAllText($temporary, "$password`n", [System.Text.UTF8Encoding]::new($false))
    & (Join-Path $bin 'initdb.exe') -D $data -U rc_admin --encoding=UTF8 --auth-host=scram-sha-256 --auth-local=trust --pwfile=$temporary
    if ($LASTEXITCODE -ne 0) { throw "initdb failed ($LASTEXITCODE); inspect the directory, do not retry over it." }
} finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
}
New-Item -ItemType Directory -Path $secretDir -Force | Out-Null
[System.IO.File]::WriteAllText($passFile, "127.0.0.1:$($port):*:rc_admin:$password`n", [System.Text.UTF8Encoding]::new($false))
$password = $null
Write-Host "Credential stored only in local user profile: $passFile (do not commit or share)."
if ($InitializeOnly) { Write-Host 'Initialized but not started.'; exit 0 }
New-Item -ItemType Directory -Path (Split-Path -Parent $log) -Force | Out-Null
$env:PGPASSFILE = $passFile
& (Join-Path $bin 'pg_ctl.exe') start -D $data -l $log -o "-h 127.0.0.1 -p $port" -w -t 120
if ($LASTEXITCODE -ne 0) { throw "pg_ctl failed ($LASTEXITCODE); inspect $log; do not retry blindly." }
& (Join-Path $bin 'pg_isready.exe') -h 127.0.0.1 -p $port -U rc_admin -d postgres
if ($LASTEXITCODE -ne 0) { throw 'RC cluster started but readiness check failed.' }
Write-Host 'RC PostgreSQL 17.11 is ready on 127.0.0.1:55449. Keep this PowerShell open until the integration check completes.'
