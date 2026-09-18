param(
    [string]$Username = "admin",
    [string]$DisplayName = "Администратор"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$encodedName = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($DisplayName))
$password = Read-Host "Пароль preview-администратора (минимум 12 символов)" -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($password)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    if ($plain.Length -lt 12) { throw "Пароль должен содержать минимум 12 символов" }
    $env:FIRST_ADMIN_PASSWORD = $plain
    $env:FIRST_ADMIN_DISPLAY_NAME_B64 = $encodedName
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    docker compose -p universal-supplier-admin-preview -f docker-compose.admin-preview.yml run --rm --no-deps `
        -e FIRST_ADMIN_PASSWORD -e FIRST_ADMIN_DISPLAY_NAME_B64 -e PYTHONUTF8=1 -e PYTHONIOENCODING=utf-8 `
        web python scripts/create_admin.py --username $Username
    if ($LASTEXITCODE -ne 0) { throw "Не удалось создать preview-администратора" }
} finally {
    if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    Remove-Item Env:FIRST_ADMIN_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:FIRST_ADMIN_DISPLAY_NAME_B64 -ErrorAction SilentlyContinue
}
