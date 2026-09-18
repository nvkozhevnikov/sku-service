[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$BackupPath)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Backup = Get-Item -LiteralPath $BackupPath
if ($Backup.Directory.FullName -ne (Join-Path $Root "backups")) {
    throw "Restore-test dump must be in the project backups directory"
}
$Name = $Backup.Name
$RestoreDb = "universal_supplier_restore_" + (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")
$Docker = if (Get-Command docker -ErrorAction SilentlyContinue) {
    (Get-Command docker).Source
} else {
    "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
}

Push-Location $Root
try {
    & $Docker compose exec -T -e "RESTORE_DB=$RestoreDb" postgres sh -eu -c 'createdb -U "$POSTGRES_USER" "$RESTORE_DB"; pg_restore -U "$POSTGRES_USER" -d "$RESTORE_DB" "/backups/$1"' sh $Name
    if ($LASTEXITCODE -ne 0) { throw "pg_restore failed" }
    $Counts = & $Docker compose exec -T -e "RESTORE_DB=$RestoreDb" postgres sh -eu -c 'psql -U "$POSTGRES_USER" -d "$RESTORE_DB" -At -F, -c "SELECT (SELECT count(*) FROM source_products),(SELECT count(*) FROM offers),(SELECT count(*) FROM product_matches),(SELECT count(*) FROM catalog_products)"'
    if ($LASTEXITCODE -ne 0) { throw "Restore count verification failed" }
    [pscustomobject]@{ restore_database = $RestoreDb; counts = ($Counts.Trim()); result = "PASS" } | ConvertTo-Json
} finally {
    & $Docker compose exec -T -e "RESTORE_DB=$RestoreDb" postgres sh -eu -c 'dropdb -U "$POSTGRES_USER" --if-exists "$RESTORE_DB"' | Out-Null
    Pop-Location
}

