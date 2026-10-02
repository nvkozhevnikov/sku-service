param([switch]$FromClipboard)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:UV_CACHE_DIR = Join-Path $root '.uv_stage5b_cache'
$existing = Get-Item 'Env:BITRIX_WEBHOOK_URL' -ErrorAction SilentlyContinue
if ($FromClipboard) {
    $clipboardValue = Get-Clipboard -Raw
    if (-not $clipboardValue) { throw 'Clipboard is empty.' }
    $env:BITRIX_WEBHOOK_URL = $clipboardValue.Trim()
    uv run --offline --with-requirements requirements.txt python scripts\run_getonly_sterbrust_snapshot.py
    $result = $LASTEXITCODE
    if ($existing) { $env:BITRIX_WEBHOOK_URL = $existing.Value } else { Remove-Item 'Env:BITRIX_WEBHOOK_URL' -ErrorAction SilentlyContinue }
    exit $result
}
uv run --offline --with-requirements requirements.txt python scripts\run_getonly_sterbrust_snapshot.py
exit $LASTEXITCODE
