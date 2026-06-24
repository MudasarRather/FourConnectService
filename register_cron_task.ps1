<#
  Registers the FourConnect HR maintenance cron (tasks_cron.py) with Windows
  Task Scheduler to run DAILY. tasks_cron.py is idempotent and now also drives
  leave accrual (monthly) + carry-forward (on 01-Apr), so a daily run keeps
  leave balances managed automatically with zero manual steps.

  Run from an ELEVATED PowerShell (Run as administrator):
      powershell -ExecutionPolicy Bypass -File C:\Projects\FourConnectService\register_cron_task.ps1

  Re-running updates the existing task in place. Default run time: 01:05 daily.
#>
param(
  [string]$Time = "01:05",
  [string]$TaskName = "FourConnect HR Maintenance"
)

$ErrorActionPreference = "Stop"

$python = "C:\Users\91700\AppData\Local\Programs\Python\Python314\python.exe"
$script = "C:\Projects\FourConnectService\tasks_cron.py"
$workdir = "C:\Projects\FourConnectService"

if (-not (Test-Path $python)) { throw "Python not found at $python" }
if (-not (Test-Path $script)) { throw "tasks_cron.py not found at $script" }

$action  = New-ScheduledTaskAction -Execute $python -Argument "`"$script`"" -WorkingDirectory $workdir
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
# Run whether or not the user is logged on; highest privileges; don't stop on battery.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
             -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' to run daily at $Time."
Write-Host "Runs: $python `"$script`""
Write-Host ""
Write-Host "Verify : Get-ScheduledTask -TaskName '$TaskName'"
Write-Host "Run now: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Remove : Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
