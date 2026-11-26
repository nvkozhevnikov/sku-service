[CmdletBinding()]
param([string]$Approval,[string]$Sha256,[string]$ConfirmSha,[switch]$Start,[switch]$Probe)
$ErrorActionPreference='Stop'
if ($Start -and $Probe) { throw 'CHOOSE_PROBE_OR_APPROVED_START' }
if ($Start -and ($Sha256 -cnotmatch '^[a-f0-9]{64}$' -or $ConfirmSha -cne $Sha256)) { throw 'AUTOMATION_EXACT_OPERATOR_CONFIRMATION_REQUIRED' }
$stbRepo=Split-Path -Parent $PSScriptRoot
$stbPython=Join-Path (Split-Path -Parent $stbRepo) 'sku-service\.venv-audit\Scripts\python.exe'
$stbBase=& $stbPython -c 'import sys; print(sys._base_executable)'
if ($LASTEXITCODE -ne 0) { throw 'EXISTING_INTERPRETER_NOT_FOUND' }
$stbPythonw=Join-Path (Split-Path -Parent $stbBase.Trim()) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $stbPythonw)) { throw 'EXISTING_WINDOWLESS_INTERPRETER_NOT_FOUND' }
$stbHostScript=Join-Path $PSScriptRoot 'one_shot_supplier_host.py'
if (-not $Probe) {
    if ($Sha256 -cnotmatch '^[a-f0-9]{64}$' -or -not $Approval) { throw 'APPROVAL_AND_SHA_REQUIRED' }
    $Approval=(Resolve-Path -LiteralPath $Approval).Path
    if ($Approval.Contains('"') -or $Approval.Contains("`n") -or $Approval.Contains("`r")) { throw 'INVALID_APPROVAL_PATH' }
    if ((Get-FileHash -LiteralPath $Approval -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Sha256) { throw 'AUTOMATION_APPROVAL_SHA_CHANGED' }
    $stbTimeout=[Environment]::GetEnvironmentVariable('PGCONNECT_TIMEOUT','Process')
    try {
        $env:PGCONNECT_TIMEOUT='5'
        $stbInspect=& $stbPython (Join-Path $PSScriptRoot 'supplier_system.py') inspect --approval $Approval --sha256 $Sha256
        if ($LASTEXITCODE -ne 0) { throw 'AUTOMATION_READ_ONLY_PREFLIGHT_FAILED' }
    } finally {
        if ([string]::IsNullOrEmpty($stbTimeout)) { Remove-Item Env:PGCONNECT_TIMEOUT -ErrorAction SilentlyContinue } else { $env:PGCONNECT_TIMEOUT=$stbTimeout }
    }
    $stbState=$stbInspect | ConvertFrom-Json
    if (@($stbState.active_jobs).Count -gt 0) { throw 'AUTOMATION_PREFLIGHT_NOT_IDLE' }
    if ($stbState.live_blocked) { throw $stbState.live_blocked }
    if (-not $Start) { Write-Output $stbInspect; Write-Output 'INSPECT ONLY: no process, HTTP, enqueue or business writes'; return }
    $stbGrant=Get-Content -LiteralPath $Approval -Raw | ConvertFrom-Json
    $stbNow=[DateTimeOffset]::UtcNow
    if ($stbNow -lt [DateTimeOffset]$stbGrant.not_before -or $stbNow -ge ([DateTimeOffset]$stbGrant.execution_until).AddMinutes(-5)) { throw 'AUTOMATION_EXECUTION_WINDOW_EXPIRED' }
}
# Windows' local WMI provider hosts this one process independently of Codex's
# child-process JobObject. The child verifies membership and fails closed.
$stbOutput=Join-Path $stbRepo ('reports\automation-launch\'+[guid]::NewGuid().ToString())
$stbCommand='"'+$stbPythonw+'" -s -u "'+$stbHostScript+'" --output "'+$stbOutput+'"'
if ($Probe) { $stbCommand+=' --probe' } else { $stbCommand+=' --approval "'+$Approval+'" --sha256 '+$Sha256+' --confirm '+$ConfirmSha }
$stbStartup=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0}
$stbCreated=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=$stbCommand;CurrentDirectory=$stbRepo;ProcessStartupInformation=$stbStartup}
if ($stbCreated.ReturnValue -ne 0) { throw ('WINDOWS_ONE_SHOT_HOST_FAILED:'+ $stbCreated.ReturnValue) }
[pscustomobject]@{pid=$stbCreated.ProcessId;output=$stbOutput;probe=[bool]$Probe;no_task_or_cron=$true;no_restart=$true;note='Creation is not completion: check HOST.json and native SESSION/FINAL/SETTINGS_RESTORED.'} | ConvertTo-Json
