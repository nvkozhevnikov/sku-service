[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Approval,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$Sha256,
    [string]$ConfirmSha,
    [switch]$Start,
    [string]$Python
)
$ErrorActionPreference='Stop'
$repoPath=Split-Path -Parent $PSScriptRoot
$approvalPath=(Resolve-Path -LiteralPath $Approval).Path
if ((Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Sha256) { throw 'AUTOMATION_APPROVAL_SHA_CHANGED' }
if (-not $Python) { $Python=Join-Path (Split-Path -Parent $repoPath) 'sku-service\.venv-audit\Scripts\python.exe' }
$pythonPath=(Resolve-Path -LiteralPath $Python).Path
$pythonWindowless=Join-Path (Split-Path -Parent $pythonPath) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw 'WINDOWLESS_PYTHON_REQUIRED' }
$runnerPath=Join-Path $PSScriptRoot 'supplier_system.py'
# Inspect uses SELECT only and rejects HEAD/engine/target drift. It does not approve LIVE.
$inspection=& $pythonPath $runnerPath inspect --approval $approvalPath --sha256 $Sha256
if ($LASTEXITCODE -ne 0) { throw 'AUTOMATION_READ_ONLY_PREFLIGHT_FAILED' }
$state=$inspection | ConvertFrom-Json
$value=Get-Content -LiteralPath $approvalPath -Raw | ConvertFrom-Json
$end=[DateTimeOffset]$value.execution_until
$end=$end.ToUniversalTime()
$remaining=($end-[DateTimeOffset]::UtcNow).TotalSeconds
if ($remaining -le 360 -or $remaining -gt 86400) { throw 'AUTOMATION_WINDOW_INVALID' }
$taskName='STERBRUST-SUPPLIERS-'+$Sha256.Substring(0,16)
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$principal=New-ScheduledTaskPrincipal -UserId $sid -LogonType Interactive -RunLevel Limited
$arguments='-u "'+$runnerPath+'" run --approval "'+$approvalPath+'" --sha256 '+$Sha256+' --confirm '+$Sha256
$action=New-ScheduledTaskAction -Execute $pythonWindowless -Argument $arguments -WorkingDirectory $repoPath
# No triggers, cron, automatic retry, WakeToRun, boot start or Windows security changes.
$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds ([math]::Floor($remaining-60))) -MultipleInstances IgnoreNew -DontStopIfGoingOnBatteries
$settings.RestartCount=0
$task=New-ScheduledTask -Action $action -Principal $principal -Settings $settings -Description 'STERBRUST bounded supplier execution; exact approval; no automatic restart'
$planPath=Join-Path $repoPath ('reports\automation-task-plans\'+$Sha256+'.xml')
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $planPath) | Out-Null
$task | Export-ScheduledTask | Set-Content -LiteralPath $planPath -Encoding UTF8
if (-not $Start) { Write-Output ('PREVIEW ONLY; no registered task. '+$planPath); Write-Output $inspection; return }
if ($ConfirmSha -cne $Sha256) { throw 'AUTOMATION_EXACT_OPERATOR_CONFIRMATION_REQUIRED' }
if ($state.live_blocked) { throw $state.live_blocked }
if ($state.active_jobs.Count -gt 0) { throw 'AUTOMATION_PREFLIGHT_NOT_IDLE' }
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw 'AUTOMATION_TASK_EXISTS_NO_IMPLICIT_RESTART' }
Register-ScheduledTask -TaskName $taskName -InputObject $task | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output ('Started one bounded task: '+$taskName+'; reports/automation-runtime; no automatic restart')
