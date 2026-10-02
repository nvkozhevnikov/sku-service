param(
    [string]$Username = 'qa-operator',
    [switch]$EnableQaWrite,
    [switch]$RestartOwnUi,
    [switch]$StartLocalDatabases
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$bin = 'C:\Users\man503\AppData\Local\Temp\universal_supplier_stage4_pg17_runtime\bin'
$stage4Data = 'C:\Users\man503\AppData\Local\Temp\universal_supplier_stage4_pg17_data'
$mainData = Join-Path $root 'work\tmp\main_snapshot_pg17_data'
$manifest = Join-Path $root 'reports\STAGE5D_2026-09-24\candidate_manifest_http_dry_run.json'
$fullManifest = Join-Path $root 'reports\WORKING_V1_2026-09-29\full_candidate_manifest.json'
$xml = Join-Path $root 'reports\STAGE5D_2026-09-25\final_qa\STAGE5D_COMMERCIAL_QA_POST_RECOVERY.xml'
$currentXml = Join-Path $root 'reports\WORKING_V1_2026-09-29\DIAGNOSTIC_QA.xml'
$uiJournalDir = Join-Path $root 'reports\STAGE5D_2026-09-25\ui_runs'
if (Test-Path $currentXml) { $xml = $currentXml }

if (-not (Test-Path $manifest) -or -not (Test-Path $xml)) { throw 'Не найдены утверждённый manifest или QA XML.' }
if ($RestartOwnUi) {
    $listeners = @(Get-NetTCPConnection -LocalPort 58097 -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 1) { throw 'Несколько слушателей на 58097; автоматическая остановка запрещена.' }
    if ($listeners.Count -eq 1) {
        if (Test-Path $uiJournalDir) {
            foreach ($events in @(Get-ChildItem -LiteralPath $uiJournalDir -Recurse -File -Filter 'events.jsonl')) {
                $lastEvent = Get-Content -LiteralPath $events.FullName -Tail 1 | ConvertFrom-Json
                if ($lastEvent.status -in @('queued', 'running')) {
                    throw "Ручной сбор ещё активен ($($lastEvent.id)); панель не будет остановлена."
                }
            }
        }
        $listener = $listeners[0]
        if ($listener.LocalAddress -ne '127.0.0.1') { throw 'Панель слушает не только localhost; автоматическая остановка запрещена.' }
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction Stop
        if (-not $process -or $process.Name -notin @('python.exe', 'pythonw.exe') -or
            $process.CommandLine -notmatch 'run_commercial_qa_ui\.py') {
            throw 'Процесс на 58097 не подтверждён как локальная QA-панель. Остановите его вручную.'
        }
        Stop-Process -Id $listener.OwningProcess -ErrorAction Stop
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            if (-not (Get-NetTCPConnection -LocalPort 58097 -State Listen -ErrorAction SilentlyContinue)) { break }
            Start-Sleep -Milliseconds 500
        }
    }
}
if (Get-NetTCPConnection -LocalPort 58097 -State Listen -ErrorAction SilentlyContinue) { throw 'Порт 58097 уже занят. Не запускайте второй экземпляр панели.' }
if (-not (Test-Path "$bin\pg_isready.exe")) { throw 'Не найден portable PostgreSQL runtime Stage 4.' }

function Test-KnownCluster([string]$Data, [string]$ExpectedSystemId) {
    if (-not (Test-Path -LiteralPath (Join-Path $Data 'PG_VERSION'))) { throw "Не найден проверенный каталог PostgreSQL: $Data" }
    $version = (Get-Content -LiteralPath (Join-Path $Data 'PG_VERSION') -Raw).Trim()
    if ($version -ne '17') { throw "Неожиданная major-версия PostgreSQL: $Data" }
    $control = & "$bin\pg_controldata.exe" $Data
    if ($LASTEXITCODE -ne 0) { throw "Не удалось проверить control file: $Data" }
    $found = $control | Select-String -Pattern "Database system identifier:\s*(\d+)" | Select-Object -First 1
    if (-not $found -or $found.Matches[0].Groups[1].Value -ne $ExpectedSystemId) {
        throw "System identifier каталога не совпал: $Data"
    }
}

function Assert-SqlIdentity([string]$Data, [string]$ExpectedSystemId, [int]$Port, [string]$Database, [string]$Role) {
    # Runtime roles deliberately cannot read data_directory. Tie the SQL
    # postmaster start timestamp to the *known* data directory's pid file.
    $identity = & "$bin\psql.exe" -h 127.0.0.1 -p $Port -U $Role -d $Database -At -v ON_ERROR_STOP=1 -c "SELECT current_database(),current_user,current_setting('server_version'),pg_is_in_recovery(),system_identifier,floor(extract(epoch from pg_postmaster_start_time()))::bigint FROM pg_control_system();"
    if ($LASTEXITCODE -ne 0) { throw "SQL identity на порту $Port недоступна." }
    $parts = ($identity | Select-Object -Last 1) -split '\|'
    # Windows PostgreSQL writes the data directory in the active ANSI code
    # page. PowerShell 7's UTF-8 Get-Content corrupts Cyrillic paths here.
    $pidBytes = [IO.File]::ReadAllBytes((Join-Path $Data 'postmaster.pid'))
    $postmasterLines = @([Text.Encoding]::GetEncoding(1251).GetString($pidBytes) -split "`r?`n" | Select-Object -First 6)
    if ($parts.Count -ne 6 -or $parts[0] -ne $Database -or $parts[1] -ne $Role -or
        $parts[2] -ne '17.11' -or $parts[3] -ne 'f' -or $parts[4] -ne $ExpectedSystemId -or
        $postmasterLines.Count -lt 6 -or
        $postmasterLines[1].Replace('/', '\').TrimEnd('\') -ine $Data.TrimEnd('\') -or
        $postmasterLines[2] -ne $parts[5] -or $postmasterLines[3] -ne [string]$Port -or
        $postmasterLines[5] -ne '127.0.0.1') {
        throw "SQL identity на порту $Port не соответствует разрешённому каталогу данных."
    }
}

function Start-KnownCluster([string]$Data, [string]$ExpectedSystemId, [int]$Port, [string]$Database, [string]$Role) {
    Test-KnownCluster $Data $ExpectedSystemId
    & "$bin\pg_isready.exe" -h 127.0.0.1 -p $Port -d $Database | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Assert-SqlIdentity $Data $ExpectedSystemId $Port $Database $Role
        return
    }
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0) {
        throw "На порту $Port есть непроверенный listener. Новый PostgreSQL не запускается."
    }
    if (-not $StartLocalDatabases) { return }
    $attemptLog = Join-Path $root "reports\WORKING_V1_2026-09-29\postgres_$Port.log"
    New-Item -ItemType Directory -Path (Split-Path -Parent $attemptLog) -Force | Out-Null
    & "$bin\pg_ctl.exe" start -D $Data -l $attemptLog -o "-h 127.0.0.1 -p $Port" -w -t 120
    if ($LASTEXITCODE -ne 0) { throw "Portable PostgreSQL $Port не запустился штатно. Лог: $attemptLog" }
    Assert-SqlIdentity $Data $ExpectedSystemId $Port $Database $Role
}

if ($StartLocalDatabases) {
    Write-Host 'Штатный запуск только двух известных portable-кластеров из пользовательского PowerShell. Docker и scheduler не запускаются.'
    Start-KnownCluster $stage4Data '7689025282387590508' 55447 'stage4_commercial_qa' 'stage4_runtime'
    Start-KnownCluster $mainData '7690591088719854484' 55448 'main_supplier_snapshot' 'main_snapshot_runtime'
}

# Neither database is started here.  The historical main snapshot is useful on
# its own; Stage 4 remains mandatory only for an explicitly enabled QA write.
& "$bin\pg_isready.exe" -h 127.0.0.1 -p 55448 -d main_supplier_snapshot | Out-Host
$mainReady = $LASTEXITCODE -eq 0
& "$bin\pg_isready.exe" -h 127.0.0.1 -p 55447 -d stage4_commercial_qa | Out-Host
$stage4Ready = $LASTEXITCODE -eq 0
if (-not $mainReady -and -not $stage4Ready) {
    throw 'Не готов ни historical snapshot (55448), ни Stage 4 QA (55447). Этот launcher не запускает PostgreSQL.'
}
if ($mainReady) {
    Test-KnownCluster $mainData '7690591088719854484'
    Assert-SqlIdentity $mainData '7690591088719854484' 55448 'main_supplier_snapshot' 'main_snapshot_runtime'
}
if ($stage4Ready) {
    Test-KnownCluster $stage4Data '7689025282387590508'
    Assert-SqlIdentity $stage4Data '7689025282387590508' 55447 'stage4_commercial_qa' 'stage4_runtime'
}
if (-not $mainReady) { Write-Warning 'Historical snapshot на 55448 недоступен: Partner-ST и Optimum будут помечены как не подключённые.' }
if (-not $stage4Ready) { Write-Warning 'Stage 4 QA на 55447 недоступна: Intervesp/Beka-Mak и QA write будут недоступны.' }

$plainPassword = Read-Host 'Пароль локального QA-оператора' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($plainPassword)
try { $env:COMMERCIAL_QA_UI_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
$env:COMMERCIAL_QA_UI_CONFIRM = 'YES'
$env:COMMERCIAL_QA_UI_ENABLED = 'YES'
$env:COMMERCIAL_QA_UI_USERNAME = $Username
$env:COMMERCIAL_QA_UI_SESSION_SECRET = ([guid]::NewGuid().ToString() + [guid]::NewGuid().ToString())
$env:COMMERCIAL_QA_UI_MANIFEST_PATH = $manifest
if (Test-Path $fullManifest) { $env:COMMERCIAL_QA_FULL_MANIFEST_PATH = $fullManifest }
$env:COMMERCIAL_QA_UI_ARTIFACTS_DIR = $uiJournalDir
$env:COMMERCIAL_QA_UI_DIAGNOSTIC_XML_PATH = $xml
$env:COMMERCIAL_QA_PROPOSALS_PATH = Join-Path $root 'reports\WORKING_V1_2026-09-29\PROPOSALS\PROPOSAL_TABLES.json'
$env:COMMERCIAL_QA_UI_HOST = '127.0.0.1'
$env:COMMERCIAL_QA_UI_PORT = '58097'
$env:COMMERCIAL_QA_DATA_SOURCE = 'stage4_readonly'
$env:COMMERCIAL_QA_MAIN_SNAPSHOT_SOURCE = 'restored_snapshot'
$env:UV_CACHE_DIR = Join-Path $root '.uv_stage5b_cache'
Remove-Item Env:COMMERCIAL_QA_WRITE_ENABLE -ErrorAction SilentlyContinue
Remove-Item Env:COMMERCIAL_QA_WRITE_CONFIRM -ErrorAction SilentlyContinue
if ($EnableQaWrite) {
    if (-not $stage4Ready) { throw 'QA write нельзя включить: Stage 4 QA PostgreSQL на 55447 недоступна.' }
    $env:COMMERCIAL_QA_WRITE_ENABLE = 'YES'
    $env:COMMERCIAL_QA_WRITE_CONFIRM = 'STAGE4_QA_ONLY'
}

Set-Location $root
try {
    uv run --offline --with-requirements requirements.txt python scripts\run_commercial_qa_ui.py
} finally {
    Remove-Item Env:COMMERCIAL_QA_UI_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:COMMERCIAL_QA_UI_SESSION_SECRET -ErrorAction SilentlyContinue
}
