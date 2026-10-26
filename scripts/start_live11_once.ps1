[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Approval,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$Sha256,
    [string]$ConfirmSha,
    [switch]$Start,
    [string]$Python
)
$ErrorActionPreference = 'Stop'
$repoPath = Split-Path -Parent $PSScriptRoot
$approvalPath = (Resolve-Path -LiteralPath $Approval).Path
if (-not $Python) { $Python = Join-Path (Split-Path -Parent $repoPath) 'sku-service\.venv-audit\Scripts\python.exe' }
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$pythonWindowless = Join-Path (Split-Path -Parent $pythonPath) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw 'WINDOWLESS_PYTHON_REQUIRED' }
if ((Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Sha256) { throw 'APPROVAL_SHA_CHANGED' }
$runnerPath = Join-Path $PSScriptRoot 'live11_autonomous.py'
& $pythonPath $runnerPath inspect --approval $approvalPath --sha256 $Sha256
if ($LASTEXITCODE -ne 0) { throw 'READ_ONLY_PREFLIGHT_FAILED' }
$taskName = 'STERBRUST-LIVE11-' + $Sha256.Substring(0,16)
$runPath = Join-Path $repoPath ('reports\autonomous-runs\' + $Sha256)
if (Test-Path -LiteralPath (Join-Path $runPath 'CLAIM.json')) { throw 'APPROVAL_ALREADY_CONSUMED_NO_RESTART' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
# No triggers, no boot/logon start, no failure restart, no WakeToRun/StartWhenAvailable.
# Task action IS the Python worker process (threads); hard timeout cannot orphan another worker process.
$arguments = '-u "' + $runnerPath + '" run --approval "' + $approvalPath + '" --sha256 ' + $Sha256 + ' --confirm ' + $Sha256
$action = New-ScheduledTaskAction -Execute $pythonWindowless -Argument $arguments -WorkingDirectory $repoPath
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 24) -MultipleInstances IgnoreNew -DontStopIfGoingOnBatteries
$settings.RestartCount = 0
$task = New-ScheduledTask -Action $action -Principal $principal -Settings $settings -Description 'STERBRUST isolated LIVE11 one use; exact SHA; no automatic restart'
$planPath = Join-Path $repoPath ('reports\autonomous-task-plans\' + $Sha256 + '.xml')
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $planPath) | Out-Null
$task | Export-ScheduledTask | Set-Content -LiteralPath $planPath -Encoding UTF8
if (-not $Start) {
    Write-Output "PREVIEW ONLY: $taskName; no task registered or started. XML=$planPath"
    return
}
if ($ConfirmSha -cne $Sha256) { throw 'EXACT_OPERATOR_CONFIRMATION_REQUIRED' }
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw 'TASK_ALREADY_EXISTS_DO_NOT_RESTART' }
Register-ScheduledTask -TaskName $taskName -InputObject $task | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output "One-shot task started: $taskName"
Write-Output "Reports: $runPath\hourly\latest.json and latest.csv; final status: $runPath\FINAL.json"
