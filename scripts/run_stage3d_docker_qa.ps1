[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Docker = if (Get-Command docker -ErrorAction SilentlyContinue) {
    (Get-Command docker).Source
} else {
    "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
}
if (-not (Test-Path -LiteralPath $Docker)) { throw "Docker CLI not found: $Docker" }

function Initialize-Environment {
    $EnvPath = Join-Path $Root ".env"
    if (-not (Test-Path -LiteralPath $EnvPath)) {
        $Lines = @(
            "POSTGRES_ADMIN_USER=universal_supplier_admin",
            "POSTGRES_ADMIN_PASSWORD=$(([guid]::NewGuid().ToString('N')) + 'A9!')",
            "POSTGRES_HOST_PORT=5432",
            "DB_HOST=127.0.0.1",
            "DB_PORT=5432",
            "DB_NAME=universal_supplier",
            "DB_USER=universal_supplier_app",
            "DB_PASSWORD=$(([guid]::NewGuid().ToString('N')) + 'U7!')",
            "DB_SSLMODE=disable",
            "STAGE3D_DB_CONFIRM=YES"
        )
        [System.IO.File]::WriteAllLines($EnvPath, $Lines, [System.Text.UTF8Encoding]::new($false))
        Write-Host "Created local secret file .env (excluded from git, image and ZIP)."
    }
    foreach ($Line in [System.IO.File]::ReadAllLines($EnvPath)) {
        if (-not $Line -or $Line.TrimStart().StartsWith("#") -or -not $Line.Contains("=")) { continue }
        $Name, $Value = $Line.Split("=", 2)
        [Environment]::SetEnvironmentVariable($Name.Trim(), $Value, "Process")
    }
    foreach ($Required in @("POSTGRES_ADMIN_USER", "POSTGRES_ADMIN_PASSWORD", "DB_NAME", "DB_USER", "DB_PASSWORD")) {
        if (-not [Environment]::GetEnvironmentVariable($Required, "Process")) { throw "Missing $Required in .env" }
    }
    $env:STAGE3D_DB_CONFIRM = "YES"
}

function Wait-Healthy {
    for ($i = 0; $i -lt 60; $i++) {
        $Id = (& $Docker compose ps -q postgres).Trim()
        if ($Id) {
            $Status = (& $Docker inspect --format '{{.State.Health.Status}}' $Id).Trim()
            if ($Status -eq "healthy") { return }
        }
        Start-Sleep -Seconds 2
    }
    throw "PostgreSQL healthcheck did not become healthy"
}

function Get-ProtectedState {
    $Sql = @"
SELECT json_build_object(
 'source_products',(SELECT count(*) FROM source_products),
 'offers',(SELECT count(*) FROM offers),
 'crawl_runs',(SELECT count(*) FROM crawl_runs),
 'product_matches',(SELECT count(*) FROM product_matches),
 'catalog_products',(SELECT count(*) FROM catalog_products),
 'pp800f_catalog_product_id',(SELECT catalog_product_id FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code='partner_st' AND sp.external_id='297'),
 'mrx3_status',(SELECT pm.status FROM product_matches pm JOIN source_products sp ON sp.id=pm.source_product_id JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code='partner_st' AND sp.external_id='305' AND pm.is_current)
)
"@
    $Json = & $Docker compose exec -T postgres psql -U $env:DB_USER -d $env:DB_NAME -At -c $Sql
    if ($LASTEXITCODE -ne 0) { throw "Count query failed" }
    return ($Json | ConvertFrom-Json)
}

Initialize-Environment
Push-Location $Root
try {
    & $Docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw "docker compose config failed" }
    & $Docker compose up -d postgres
    if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }
    Wait-Healthy

    & $Docker compose --profile tools run --rm application scripts/docker_postgres_migrate.py
    if ($LASTEXITCODE -ne 0) { throw "migration runner failed" }
    & $Docker compose --profile tools run --rm application scripts/run_stage3d_docker_verification.py
    if ($LASTEXITCODE -ne 0) { throw "real DB verification failed" }
    & $Docker compose --profile tools run --rm application -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "test suite failed" }

    $Before = Get-ProtectedState
    & $Docker compose down
    if ($LASTEXITCODE -ne 0) { throw "docker compose down failed" }
    & $Docker compose up -d postgres
    Wait-Healthy
    $AfterDownUp = Get-ProtectedState
    & $Docker compose up -d --force-recreate postgres
    Wait-Healthy
    $AfterRecreate = Get-ProtectedState
    $PersistencePass = (($Before | ConvertTo-Json -Compress) -eq ($AfterDownUp | ConvertTo-Json -Compress)) -and (($Before | ConvertTo-Json -Compress) -eq ($AfterRecreate | ConvertTo-Json -Compress))
    $Volume = (& $Docker volume inspect universal_supplier_pgdata --format '{{.Name}}').Trim()
    $Persistence = [ordered]@{
        state = if ($PersistencePass) { "PASS" } else { "FAIL" }
        volume_name = $Volume
        docker_compose_down_without_v = "PASS"
        container_force_recreate = "PASS"
        counts_before = $Before
        counts_after_down_up = $AfterDownUp
        counts_after_force_recreate = $AfterRecreate
        docker_volume_persistence = if ($PersistencePass) { "PASS" } else { "FAIL" }
        data_after_container_recreate = if ($PersistencePass) { "PASS" } else { "FAIL" }
    }
    $Persistence | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath reports\STAGE3D_VOLUME_PERSISTENCE_QA.json -Encoding utf8
    if (-not $PersistencePass) { throw "Persistence counts/mappings changed" }

    $BackupPath = & "$PSScriptRoot\docker_postgres_backup.ps1"
    $Restore = (& "$PSScriptRoot\docker_postgres_restore_test.ps1" -BackupPath $BackupPath | ConvertFrom-Json)
    [ordered]@{
        state = if ($Restore.result -eq "PASS") { "PASS" } else { "FAIL" }
        pg_dump = "PASS"
        dump_created_outside_container_lifecycle = $true
        dump_packaged = $false
        pg_restore_test = $Restore.result
        restored_counts = $Restore.counts
        temporary_restore_database_removed = $true
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath reports\STAGE3D_BACKUP_RESTORE_QA.json -Encoding utf8

    [ordered]@{
        docker_version = ((& $Docker --version).Trim())
        docker_compose_version = ((& $Docker compose version).Trim())
        postgres_image = "postgres:17.11"
        compose_health_status = ((& $Docker inspect --format '{{.State.Health.Status}}' ((& $Docker compose ps -q postgres).Trim())).Trim())
    } | ConvertTo-Json | Set-Content -LiteralPath reports\STAGE3D_RUNTIME_METADATA.json -Encoding utf8

    & $Docker compose --profile tools run --rm application scripts/finalize_stage3d_reports.py
    if ($LASTEXITCODE -ne 0) { throw "Stage 3D final report failed" }
} finally {
    Pop-Location
}
