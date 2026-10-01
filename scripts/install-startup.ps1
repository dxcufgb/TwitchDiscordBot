<#
.SYNOPSIS
    Registers (or removes) a Windows scheduled task that starts the bot
    automatically and restarts it if it crashes.

.EXAMPLE
    # From an elevated (Run as Administrator) PowerShell:
    powershell -ExecutionPolicy Bypass -File .\scripts\install-startup.ps1
    # Start at user logon instead of at boot (no admin rights needed):
    powershell -ExecutionPolicy Bypass -File .\scripts\install-startup.ps1 -AtLogon
    # Remove:
    powershell -ExecutionPolicy Bypass -File .\scripts\install-startup.ps1 -Uninstall

.NOTES
    -AtStartup (default) runs the bot at boot, before anyone logs in, as the
    current user (S4U logon, no password stored). This requires admin rights.
    The task runs start-bot.cmd --watch, so the bot is restarted if it exits.
    Output is appended to bot-console.log in the bot folder.
#>
[CmdletBinding()]
param(
    [switch]$AtLogon,
    [switch]$Uninstall,
    [string]$TaskName = 'TwitchDiscordBot'
)
$ErrorActionPreference = 'Stop'

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task '$TaskName'."
    return
}

$botDir = Split-Path -Parent $PSScriptRoot
$startScript = Join-Path $botDir 'start-bot.cmd'
$logFile = Join-Path $botDir 'bot-console.log'
if (-not (Test-Path $startScript)) { throw "start-bot.cmd not found in $botDir" }

$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute 'cmd.exe' `
    -Argument "/c `"`"$startScript`" --watch >> `"$logFile`" 2>&1`"" `
    -WorkingDirectory $botDir

if ($AtLogon) {
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
} else {
    $isAdmin = ([Security.Principal.WindowsPrincipal]::new(
        [Security.Principal.WindowsIdentity]::GetCurrent())
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) { throw 'Run this from an elevated PowerShell, or use -AtLogon.' }
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $trigger.Delay = 'PT30S'   # give the network a moment to come up
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
}

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' for $user ($(if ($AtLogon) {'at logon'} else {'at startup'}))."
Write-Host "Start it now with:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Console output:     $logFile"
