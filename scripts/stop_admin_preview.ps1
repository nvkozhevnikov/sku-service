param(
    [switch]$RemovePreviewData
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$PreviewVolume = "universal_supplier_admin_preview_pgdata"
$WorkingVolume = "universal_supplier_pgdata"

python "$Root\scripts\preview_volume_guard.py" $PreviewVolume
if ($LASTEXITCODE -ne 0) { throw "preview volume guard failed" }

docker rm -f universal-supplier-admin-preview-packaged-web 2>$null | Out-Null
docker compose -p universal-supplier-admin-preview -f docker-compose.admin-preview.yml down
if ($RemovePreviewData) {
    if ($PreviewVolume -eq $WorkingVolume) { throw "REFUSING to delete $WorkingVolume" }
    $actual = docker volume inspect $PreviewVolume --format "{{.Name}}" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Preview volume $PreviewVolume not found" }
    if ($actual.Trim() -ne $PreviewVolume) { throw "Unexpected preview volume: $actual" }
    docker volume rm $PreviewVolume
    Write-Host "Removed preview volume $PreviewVolume only"
} else {
    Write-Host "Preview containers stopped. Preview volume kept: $PreviewVolume"
}
Write-Host "Working volume $WorkingVolume was not modified"
