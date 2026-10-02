# Manual restart of the one known persistent RC cluster. Never creates a DB.
param([switch]$RecoverStalePid)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'rc_pid_file.ps1')
. (Join-Path $PSScriptRoot 'rc_port_guard.ps1')
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$data = Join-Path (Split-Path -Parent $root) 'universal_supplier_rc_pg17_data'
$bin = 'C:\Users\man503\AppData\Local\Temp\universal_supplier_stage4_pg17_runtime\bin'
$log = Join-Path $root 'reports\RC_LOCAL\postgres.log'
$passFile = Join-Path $env:LOCALAPPDATA 'UniversalSupplier\rc-local\pgpass.conf'
$expectedId = '7691270601420084116'
if (-not (Test-Path -LiteralPath (Join-Path $data 'PG_VERSION')) -or
    (Get-Content -LiteralPath (Join-Path $data 'PG_VERSION') -Raw).Trim() -ne '17') {
    throw 'The pinned RC PostgreSQL data directory is missing or has the wrong major version.'
}
# Read the uint64 system identifier from the pinned PG17 control file. The
# human-readable pg_controldata labels are localized and cannot be an identity guard.
if ((& (Join-Path $bin 'postgres.exe') --version) -notmatch '17\.11') {
    throw 'Only the pinned PostgreSQL 17.11 binaries are allowed.'
}
$control = @(& (Join-Path $bin 'pg_controldata.exe') $data)
$controlExit = $LASTEXITCODE
$controlBytes = [IO.File]::ReadAllBytes((Join-Path $data 'global\pg_control'))
if ($controlBytes.Length -lt 8 -or -not [BitConverter]::IsLittleEndian) {
    throw 'Cannot safely read the PG17 Windows control-file system identifier.'
}
$actualId = [BitConverter]::ToUInt64($controlBytes, 0).ToString()
if ($controlExit -ne 0 -or $actualId -cne $expectedId) {
    throw 'The RC control-file system identifier changed.'
}
function Assert-NoRcPostmaster {
    # Fail closed if process/listener inventory is unavailable. A missing PID
    # alone is insufficient: a different postmaster can own the same directory.
    $processes = @(Get-CimInstance Win32_Process -Filter "Name = 'postgres.exe'" -ErrorAction Stop)
    if ($processes.Count) {
        throw 'A postgres process exists. Stale-lock recovery is refused; inspect it first.'
    }
    $recordedProcesses = @(Get-CimInstance Win32_Process -Filter ("ProcessId = " + $script:recordedPid) -ErrorAction Stop)
    $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -eq 55449)
    $netstat = @(& (Join-Path $env:SystemRoot 'System32\netstat.exe') -ano -p tcp)
    if ($LASTEXITCODE -ne 0) { throw 'Netstat inventory failed; recovery refused.' }
    $netstatCount = Get-RcNetstatPortCount -Lines $netstat -Port 55449
    # Direct connect is diagnostic. Timeout does not override two OS inventories.
    $probeResult = 'ERROR'
    $probe = New-Object System.Net.Sockets.TcpClient
    try {
        $pending = $probe.BeginConnect('127.0.0.1', 55449, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(1500)) {
            $probeResult = 'TIMEOUT'
        } else {
            try { $probe.EndConnect($pending); $probeResult = 'CONNECTED' }
            catch {
                $socketError = $_.Exception
                while ($socketError.InnerException) { $socketError = $socketError.InnerException }
                if ($socketError -is [System.Net.Sockets.SocketException] -and
                    $socketError.SocketErrorCode -eq [System.Net.Sockets.SocketError]::ConnectionRefused) {
                    $probeResult = 'CONNECTION_REFUSED'
                } else { throw }
            }
        }
    } finally { $probe.Dispose() }
    Assert-RcRecoveryEvidence -PIDExists ($recordedProcesses.Count -ne 0) -PostgresCount $processes.Count -NetTCPCount $listeners.Count -NetstatCount $netstatCount -InventoriesSucceeded $true -ProbeResult $probeResult
    $script:lastRecoveryEvidence = [PSCustomObject]@{
        PostgresCount = $processes.Count; RecordedPIDCount = $recordedProcesses.Count
        GetNetTCPListenerCount = $listeners.Count; NetstatLocalPortCount = $netstatCount
        DirectProbe = $probeResult; CheckedUTC = [DateTime]::UtcNow.ToString('o')
    }
    Write-Host ("RC recovery inventory verified: " + ($script:lastRecoveryEvidence | ConvertTo-Json -Compress))
}
if (-not (Test-Path -LiteralPath $passFile)) { throw 'The RC credential file is missing.' }
$env:PGPASSFILE = $passFile
& (Join-Path $bin 'pg_isready.exe') -h 127.0.0.1 -p 55449 -d universal_supplier_server | Out-Null
if ($LASTEXITCODE -ne 0) {
    if (Get-NetTCPConnection -LocalPort 55449 -State Listen -ErrorAction SilentlyContinue) {
        throw 'Port 55449 is occupied by an unverified listener; no start attempted.'
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $log) -Force | Out-Null
    $pidPath = Join-Path $data 'postmaster.pid'
    if (Test-Path -LiteralPath $pidPath) {
        if (-not $RecoverStalePid) {
            throw 'postmaster.pid exists. Use -RecoverStalePid only from ordinary operator PowerShell after diagnostics.'
        }
        $pidBytes = [IO.File]::ReadAllBytes($pidPath)
        $pidHash = (Get-FileHash -LiteralPath $pidPath -Algorithm SHA256).Hash
        $decodedPid = ConvertFrom-RcPidBytes -Bytes $pidBytes -ApprovedDataDirectory $data
        $staleLines = $decodedPid.Lines
        $script:recordedPid = 0
        if ($staleLines.Count -lt 6 -or -not [int]::TryParse($staleLines[0], [ref]$script:recordedPid) -or
            $script:recordedPid -le 0 -or
            $staleLines[1].Replace('/', '\').TrimEnd('\') -ine $data.TrimEnd('\') -or
            $staleLines[3] -ne '55449' -or $staleLines[5] -ne '127.0.0.1') {
            throw 'The stale PID file does not identify the approved RC directory/port.'
        }
        Assert-NoRcPostmaster
        $incident = Join-Path $root ('reports\RC_LOCAL\STALE_PID_' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'))
        New-Item -ItemType Directory -Path $incident -ErrorAction Stop | Out-Null
        $savedPid = Join-Path $incident 'postmaster.pid.saved'
        [IO.File]::WriteAllBytes($savedPid, $pidBytes)
        [PSCustomObject]@{
            DataDirectory = $data; SystemIdentifier = $actualId; RecordedPid = $script:recordedPid
            PIDFileSHA256 = $pidHash; SavedAtUTC = [DateTime]::UtcNow.ToString('o')
            PIDFileEncoding = $decodedPid.Encoding
            PIDFileLastWriteUTC = (Get-Item -LiteralPath $pidPath).LastWriteTimeUtc.ToString('o')
            PowerShellVersion = $PSVersionTable.PSVersion.ToString(); User = [Security.Principal.WindowsIdentity]::GetCurrent().Name
            ProcessInventory = $script:lastRecoveryEvidence
        } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $incident 'metadata.json') -Encoding UTF8
        if ((Get-FileHash -LiteralPath $savedPid -Algorithm SHA256).Hash -ne $pidHash) {
            throw 'Stale PID evidence copy verification failed.'
        }
        Assert-NoRcPostmaster
        if ((Get-FileHash -LiteralPath $pidPath -Algorithm SHA256).Hash -ne $pidHash) {
            throw 'PID file changed during diagnosis; recovery refused.'
        }
        # Move the single verified lock into the evidence directory; no deletion.
        Move-Item -LiteralPath $pidPath -Destination (Join-Path $incident 'postmaster.pid.original') -ErrorAction Stop
        $log = Join-Path $incident 'postgres_recovery.log'
        Write-Host "Stale lock preserved at $incident. Attempting one normal pg_ctl start."
    }
    & (Join-Path $bin 'pg_ctl.exe') start -D $data -l $log -o '-h 127.0.0.1 -p 55449' -w -t 120
    if ($LASTEXITCODE -ne 0) { throw 'pg_ctl failed; inspect the log. Do not delete postmaster.pid.' }
}
$identity = & (Join-Path $bin 'psql.exe') -X -A -t -h 127.0.0.1 -p 55449 -U rc_admin -d universal_supplier_server -v ON_ERROR_STOP=1 -c "BEGIN READ ONLY; SELECT current_database(),current_user,current_setting('server_version'),pg_is_in_recovery(),system_identifier,current_setting('port') FROM pg_control_system(); COMMIT;"
$identityExit = $LASTEXITCODE
$runningPid = ConvertFrom-RcPidBytes -Bytes ([IO.File]::ReadAllBytes((Join-Path $data 'postmaster.pid'))) -ApprovedDataDirectory $data
$pidLines = @($runningPid.Lines | Select-Object -First 6)
if ($identityExit -ne 0 -or -not ($identity -contains "universal_supplier_server|rc_admin|17.11|f|$expectedId|55449")) {
    throw 'Started listener does not match the approved RC PostgreSQL identity.'
}
if ($pidLines.Count -lt 6 -or $pidLines[1].Replace('/', '\').TrimEnd('\') -ine $data.TrimEnd('\') -or
    $pidLines[3] -ne '55449' -or $pidLines[5] -ne '127.0.0.1') {
    throw 'Running RC postmaster does not match the approved data directory and host/port.'
}
Write-Host 'Dedicated RC PostgreSQL 17.11 on 127.0.0.1:55449 is ready.'
& (Join-Path $bin 'psql.exe') -X -h 127.0.0.1 -p 55449 -U rc_admin -d universal_supplier_server -v ON_ERROR_STOP=1 -c "BEGIN READ ONLY; SELECT version(), current_database(); SELECT 'suppliers' AS table_name,count(*) FROM suppliers UNION ALL SELECT 'source_products',count(*) FROM source_products UNION ALL SELECT 'offers',count(*) FROM offers UNION ALL SELECT 'product_matches',count(*) FROM product_matches UNION ALL SELECT 'catalog_products',count(*) FROM catalog_products UNION ALL SELECT 'supplier_http_captures',count(*) FROM supplier_http_captures UNION ALL SELECT 'offer_commercial_observations',count(*) FROM offer_commercial_observations; COMMIT;"
if ($LASTEXITCODE -ne 0) { throw 'RC read-only counts failed; do not continue ingestion.' }
