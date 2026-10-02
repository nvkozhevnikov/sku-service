# Read-only. No pg_ctl/start/stop/initdb, marker edits, WAL or ACL changes.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'rc_pid_file.ps1')
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$data = Join-Path (Split-Path -Parent $repo) 'universal_supplier_rc_pg17_data'
$bin = Join-Path $env:LOCALAPPDATA 'Temp\universal_supplier_stage4_pg17_runtime\bin'
$controlPath = Join-Path $data 'global\pg_control'
$expected = '7691270601420084116'
$control = @(& (Join-Path $bin 'pg_controldata.exe') $data 2>&1)
$controlExit = $LASTEXITCODE
$bytes = [IO.File]::ReadAllBytes($controlPath)
$binaryId = [BitConverter]::ToUInt64($bytes, 0).ToString()
$patternMatched = [bool]($control | Select-String "Database system identifier:\s*$expected")
$pidFile = Join-Path $data 'postmaster.pid'
$recordedPid = $(if (Test-Path -LiteralPath $pidFile) { [int](Get-Content -LiteralPath $pidFile -TotalCount 1) } else { 0 })
$process = $(if ($recordedPid) { Get-Process -Id $recordedPid -ErrorAction SilentlyContinue } else { $null })
$decodedPid = $(if (Test-Path -LiteralPath $pidFile) { ConvertFrom-RcPidBytes -Bytes ([IO.File]::ReadAllBytes($pidFile)) -ApprovedDataDirectory $data } else { $null })
[PSCustomObject]@{
    ReadOnly = $true
    PowerShellVersion = $PSVersionTable.PSVersion.ToString()
    DataDirectory = $data
    ExpectedSystemIdentifier = $expected
    ControlFileSystemIdentifier = $binaryId
    PgControldataExitCode = $controlExit
    OldEnglishLabelRegexMatched = $patternMatched
    ControlOutput = (($control | Select-Object -First 10) -join "`n")
    RecordedPostmasterPid = $recordedPid
    PIDFileEncoding = $(if ($decodedPid) { $decodedPid.Encoding } else { $null })
    PIDFileLines = $(if ($decodedPid) { $decodedPid.Lines } else { @() })
    RecordedPostmasterProcessName = $(if ($process) { $process.ProcessName } else { 'NOT_PRESENT' })
    RestartScriptSHA256 = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'start_local_rc_postgres.ps1') -Algorithm SHA256).Hash
    PostgresProcesses = @(Get-Process -Name postgres -ErrorAction SilentlyContinue | Select-Object Id,ProcessName)
} | ConvertTo-Json -Depth 3
