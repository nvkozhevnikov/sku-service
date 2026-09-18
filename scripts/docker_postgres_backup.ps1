[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BackupDirectory = Join-Path $Root "backups"
$BackupDirectory = [System.IO.Path]::GetFullPath($BackupDirectory)
New-Item -ItemType Directory -Force -Path $BackupDirectory | Out-Null

$Stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$Name = "universal_supplier_$Stamp.dump"
$Docker = if (Get-Command docker -ErrorAction SilentlyContinue) {
    (Get-Command docker).Source
} else {
    "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
}

Push-Location $Root
try {
    & $Docker compose exec -T postgres sh -eu -c 'umask 077; pg_dump -U "$APP_DB_USER" -d "$APP_DB_NAME" -Fc -f "/backups/$1"' sh $Name
    if ($LASTEXITCODE -ne 0) { throw "pg_dump failed" }
    $Path = Join-Path $BackupDirectory $Name
    if (-not (Test-Path -LiteralPath $Path) -or (Get-Item -LiteralPath $Path).Length -eq 0) {
        throw "Backup file is missing or empty: $Path"
    }
    Write-Output $Path
} finally {
    Pop-Location
}
