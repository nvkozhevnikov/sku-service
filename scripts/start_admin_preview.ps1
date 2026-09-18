param(
    [switch]$PackagedEvidence,
    [switch]$OpenBrowser,
    [int]$PreviewPort = 58096,
    [int]$PreviewPostgresPort = 55445
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$ComposeProject = "universal-supplier-admin-preview"
$ComposeFile = "docker-compose.admin-preview.yml"
$PreviewVolume = "universal_supplier_admin_preview_pgdata"
$WorkingVolume = "universal_supplier_pgdata"
$PreviewDatabase = "preview"
$PreviewRole = "preview"
$env:PREVIEW_WEB_PORT = [string]$PreviewPort
$env:PREVIEW_POSTGRES_PORT = [string]$PreviewPostgresPort

function Assert-LastExit([string]$Message) {
    if ($LASTEXITCODE -ne 0) { throw $Message }
}

function Ensure-PreviewImage {
    docker image inspect universal-supplier-stage6e1-tests 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Собираю локальный preview image (backup-каталоги исключены из build context)..."
        docker build -t universal-supplier-stage6e1-tests .
        Assert-LastExit "Не удалось собрать preview image"
    }
}

function Assert-LoopbackPortAvailable([int]$Port) {
    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, $Port)
    try {
        $listener.Start()
    } catch {
        throw "Локальный порт $Port уже занят. Укажите свободный -PreviewPort; клонирование не начато."
    } finally {
        $listener.Stop()
    }
}

function Stop-PreviewWebQuietly {
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        docker compose -p $ComposeProject -f $ComposeFile stop web 2>&1 | Out-Null
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Find-WorkingPostgresByVolume {
    $volumeCandidates = @()
    foreach ($id in @(& docker ps -q)) {
        if (-not $id) { continue }
        $item = @(& docker inspect $id | ConvertFrom-Json)[0]
        foreach ($mount in @($item.Mounts)) {
            if ($mount.Type -eq "volume" -and $mount.Name -eq $WorkingVolume -and $mount.Destination -eq "/var/lib/postgresql/data") {
                $volumeCandidates += [pscustomobject]@{ Id = $id; Name = $item.Name.TrimStart('/'); Inspect = $item }
            }
        }
    }
    if ($volumeCandidates.Count -eq 0) { throw "Рабочий PostgreSQL с volume $WorkingVolume не найден. Запустите его или явно используйте -PackagedEvidence." }
    if ($volumeCandidates.Count -gt 1) {
        $names = ($volumeCandidates | ForEach-Object { $_.Name }) -join ", "
        throw "Найдено несколько контейнеров с рабочим volume ${WorkingVolume}: $names. Выбор запрещён."
    }
    return $volumeCandidates[0]
}

function Container-Environment($inspect) {
    $result = @{}
    foreach ($entry in @($inspect.Config.Env)) {
        $pair = $entry -split "=", 2
        if ($pair.Count -eq 2) { $result[$pair[0]] = $pair[1] }
    }
    return $result
}

function Find-ApplicationDatabase([string]$Container, [string]$AdminRole, [string]$MaintenanceDatabase) {
    $databases = @(& docker exec $Container psql -X -U $AdminRole -d $MaintenanceDatabase -At -v ON_ERROR_STOP=1 -c "SELECT datname FROM pg_database WHERE datistemplate=false AND datallowconn ORDER BY datname")
    Assert-LastExit "Не удалось получить список баз из рабочего PostgreSQL"
    $databaseCandidates = @()
    foreach ($database in $databases) {
        $database = $database.Trim()
        if (-not $database) { continue }
        $hasSchema = (& docker exec $Container psql -X -U $AdminRole -d $database -At -v ON_ERROR_STOP=1 -c "SELECT CASE WHEN to_regclass('public.source_products') IS NOT NULL AND to_regclass('public.offers') IS NOT NULL AND to_regclass('public.catalog_products') IS NOT NULL AND to_regclass('public.catalog_offer_selection') IS NOT NULL THEN 1 ELSE 0 END").Trim()
        Assert-LastExit "Не удалось проверить схему базы $database"
        if ($hasSchema -eq "1") { $databaseCandidates += $database }
    }
    if ($databaseCandidates.Count -eq 0) { throw "База с полной схемой Universal Supplier не найдена; клонирование остановлено." }
    if ($databaseCandidates -contains $MaintenanceDatabase) {
        return $MaintenanceDatabase
    }
    if ($databaseCandidates.Count -gt 1) {
        $activity = @{}
        $activitySql = "SELECT datname||'|'||xact_commit FROM pg_stat_database WHERE datname IS NOT NULL"
        foreach ($row in @(& docker exec $Container psql -X -U $AdminRole -d $MaintenanceDatabase -At -v ON_ERROR_STOP=1 -c $activitySql)) {
            $pair = $row -split "\|", 2
            if ($pair.Count -eq 2 -and $pair[1] -match '^\d+$') { $activity[$pair[0]] = [long]$pair[1] }
        }
        Assert-LastExit "Не удалось прочитать статистику активности баз"
        $ranked = @($databaseCandidates | ForEach-Object {
            [pscustomobject]@{ Name = $_; Commits = $(if ($activity.ContainsKey($_)) { $activity[$_] } else { 0 }) }
        } | Sort-Object -Property @{Expression='Commits';Descending=$true}, @{Expression='Name';Descending=$false})
        if ($ranked.Count -gt 1 -and $ranked[0].Commits -gt $ranked[1].Commits) {
            Write-Host "Рабочая база отличена от старых QA-копий по уникально максимальной накопленной активности PostgreSQL."
            return $ranked[0].Name
        }
        throw "Найдено несколько неразличимых баз со схемой Universal Supplier, а POSTGRES_DB среди них отсутствует: $($databaseCandidates -join ', '). Выбор запрещён."
    }
    return $databaseCandidates[0]
}

python "$Root\scripts\preview_volume_guard.py" $PreviewVolume
Assert-LastExit "Preview volume guard failed"
if ($PreviewVolume -eq $WorkingVolume) { throw "REFUSING working volume $WorkingVolume" }
Ensure-PreviewImage

if ($PackagedEvidence) {
    Write-Host "Запускаю явно выбранный packaged-evidence preview. Реальная БД не читается."
    docker rm -f universal-supplier-admin-preview-packaged-web 2>$null | Out-Null
    Assert-LoopbackPortAvailable $PreviewPort
    docker run -d --name universal-supplier-admin-preview-packaged-web `
        -e CONTROL_PLANE_DEMO_SNAPSHOT=YES `
        -e SESSION_SIGNING_SECRET=preview-session-secret-at-least-32-bytes `
        -e CONTROL_PLANE_ALLOWED_HOSTS=127.0.0.1,localhost `
        -e SCHEDULER_ENABLED=NO `
        -p "127.0.0.1:${PreviewPort}:8080" `
        -v "${Root}:/app" -w /app `
        universal-supplier-stage6e1-tests `
        python -m uvicorn universal_supplier.control_plane.web:app --host 0.0.0.0 --port 8080 | Out-Null
    Assert-LastExit "Не удалось запустить packaged-evidence preview"
} else {
    Stop-PreviewWebQuietly
    Assert-LoopbackPortAvailable $PreviewPort
    $source = Find-WorkingPostgresByVolume
    $sourceEnv = Container-Environment $source.Inspect
    $adminRole = $sourceEnv["POSTGRES_USER"]
    $maintenanceDatabase = $sourceEnv["POSTGRES_DB"]
    if (-not $adminRole -or -not $maintenanceDatabase) {
        throw "В рабочем контейнере не удалось определить POSTGRES_USER/POSTGRES_DB; клонирование остановлено."
    }
    $sourceDatabase = Find-ApplicationDatabase $source.Name $adminRole $maintenanceDatabase
    Write-Host "Источник выбран по volume: $($source.Name)"
    Write-Host "База выбрана по обязательным таблицам: $sourceDatabase"
    Write-Host "Рабочая БД используется только для pg_dump; DDL/DML к ней не выполняются."

    docker rm -f universal-supplier-admin-preview-packaged-web 2>$null | Out-Null
    docker compose -p $ComposeProject -f $ComposeFile up -d postgres
    Assert-LastExit "Не удалось запустить preview PostgreSQL"
    $previewContainer = (& docker compose -p $ComposeProject -f $ComposeFile ps -q postgres).Trim()
    if (-not $previewContainer) { throw "Preview PostgreSQL container не найден" }
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        docker exec $previewContainer pg_isready -U $PreviewRole -d postgres 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw "Preview PostgreSQL не готов" }

    $suffix = [Guid]::NewGuid().ToString("N")
    $sourceDump = "/tmp/universal_supplier_preview_${suffix}.dump"
    $localDump = Join-Path ([IO.Path]::GetTempPath()) "universal_supplier_preview_${suffix}.dump"
    try {
        docker exec $source.Name pg_dump -Fc --no-owner --no-acl -U $adminRole -d $sourceDatabase -f $sourceDump
        Assert-LastExit "pg_dump рабочей базы не выполнен; packaged-evidence fallback запрещён"
        docker cp "$($source.Name):$sourceDump" $localDump
        Assert-LastExit "Не удалось скопировать dump во временный каталог Windows"
        docker cp $localDump "${previewContainer}:/tmp/source.dump"
        Assert-LastExit "Не удалось скопировать dump в preview PostgreSQL"

        docker exec $previewContainer psql -X -U $PreviewRole -d postgres -v ON_ERROR_STOP=1 -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$PreviewDatabase' AND pid <> pg_backend_pid();"
        Assert-LastExit "Не удалось завершить только preview DB sessions"
        docker exec $previewContainer psql -X -U $PreviewRole -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $PreviewDatabase;"
        Assert-LastExit "Не удалось удалить только preview database"
        docker exec $previewContainer psql -X -U $PreviewRole -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $PreviewDatabase OWNER $PreviewRole;"
        Assert-LastExit "Не удалось создать чистую preview database"
        docker exec $previewContainer pg_restore -U $PreviewRole -d $PreviewDatabase --no-owner --no-acl --exit-on-error /tmp/source.dump
        Assert-LastExit "Не удалось восстановить dump в чистую preview database"
    } finally {
        if (Test-Path -LiteralPath $localDump) { Remove-Item -LiteralPath $localDump -Force }
        docker exec $source.Name rm -f $sourceDump 2>$null | Out-Null
        docker exec $previewContainer rm -f /tmp/source.dump 2>$null | Out-Null
    }

    docker compose -p $ComposeProject -f $ComposeFile run --rm --no-deps web python scripts/docker_postgres_migrate.py
    Assert-LastExit "Миграции preview database не прошли"
    $countSql = "SELECT (SELECT count(*) FROM source_products)||'|'||(SELECT count(*) FROM offers)||'|'||(SELECT count(*) FROM catalog_products)||'|'||(SELECT count(*) FROM catalog_offer_selection)"
    $counts = (& docker exec $previewContainer psql -X -U $PreviewRole -d $PreviewDatabase -At -v ON_ERROR_STOP=1 -c $countSql).Trim()
    Assert-LastExit "Не удалось проверить post-restore counts"
    $parts = $counts -split "\|"
    if ($parts.Count -ne 4 -or @($parts | Where-Object { $_ -notmatch '^\d+$' }).Count -gt 0) { throw "Post-restore counts недостоверны: $counts" }
    Write-Host "Post-restore counts (source_products|offers|catalog_products|catalog_offer_selection): $counts"
    $env:CONTROL_PLANE_DEMO_SNAPSHOT = "NO"
    docker compose -p $ComposeProject -f $ComposeFile up -d web
    Assert-LastExit "Не удалось запустить preview web"
}

$ok = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:${PreviewPort}/login" -TimeoutSec 2
        if ($response.StatusCode -eq 200) { $ok = $true; break }
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $ok) { throw "Preview web не стал доступен" }

Write-Host "Админка: http://127.0.0.1:${PreviewPort}/"
Write-Host "XML: http://127.0.0.1:${PreviewPort}/exports/xml"
if (-not $PackagedEvidence) {
    Write-Host "Если администратора ещё нет: .\scripts\create_preview_admin.ps1"
}
if ($OpenBrowser) { Start-Process "http://127.0.0.1:${PreviewPort}/exports/xml" }
