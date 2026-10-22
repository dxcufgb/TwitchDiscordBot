<#
.SYNOPSIS
    Installs, upgrades, reconfigures or removes TwitchDiscordBot on Windows.

.DESCRIPTION
    Run install.cmd (double-click) from the extracted release folder, or from an elevated PowerShell:
        powershell -ExecutionPolicy Bypass -File .\install.ps1 [options]

    The installer:
      * finds Python 3.10+ (installs it with winget if missing),
      * copies the bot to C:\ProgramData\TwitchDiscordBot and creates a virtual environment,
      * asks for the Discord/Twitch credentials and verifies them,
      * registers a scheduled task that starts the bot at boot as LOCAL SERVICE and restarts it if it stops.

.PARAMETER Reconfigure   Enter new credentials and restart.
.PARAMETER Uninstall     Remove the task and program files (keeps .env, data and logs; add -Purge to delete them too).
.PARAMETER StopService   Only stop the bot (used by the setup wizard before it replaces files).
.PARAMETER RemoveService Only stop the bot and remove its scheduled task (used by the setup wizard's uninstaller).
.PARAMETER NoService     Don't register the scheduled task.
.PARAMETER SkipCheck     Don't verify the credentials against Discord/Twitch.
.PARAMETER Yes           Never prompt; credentials come from DISCORD_TOKEN, TWITCH_APP_ID, TWITCH_APP_SECRET (OWNER_ID optional).
.PARAMETER AutoUpdate    Also turn on automatic updates (daily scheduled task running as SYSTEM). No GitHub token is needed
                         (the repository is public); an optional one (for a private fork) comes from the
                         UPDATE_GITHUB_TOKEN environment variable only.
.PARAMETER NoAutoUpdate  Turn automatic updates off. Without -AutoUpdate/-NoAutoUpdate an upgrade keeps the current choice.
.PARAMETER EnableAutoUpdate   Only turn on automatic updates (or replace or delete a stored token), then exit.
.PARAMETER DisableAutoUpdate  Only turn off automatic updates and delete a stored token, then exit.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:ProgramData 'TwitchDiscordBot'),
    [switch]$Reconfigure,
    [switch]$Uninstall,
    [switch]$StopService,
    [switch]$RemoveService,
    [switch]$Purge,
    [switch]$NoService,
    [switch]$SkipCheck,
    [switch]$Yes,
    [switch]$AutoUpdate,
    [switch]$NoAutoUpdate,
    [switch]$EnableAutoUpdate,
    [switch]$DisableAutoUpdate
)
$ErrorActionPreference = 'Stop'
$TaskName = 'TwitchDiscordBot'
$SrcDir = $PSScriptRoot
$InstallDir = $InstallDir.TrimEnd('\')
$VenvPy = Join-Path $InstallDir '.venv\Scripts\python.exe'
$EnvFile = Join-Path $InstallDir '.env'
$UpdateTaskName = 'TwitchDiscordBot Update'
$UpdaterDir = Join-Path $InstallDir 'updater'
$TokenFile = Join-Path $UpdaterDir 'github-token'

# Well-known SIDs (names are localized, SIDs are not).
$SidAdmins = '*S-1-5-32-544'; $SidSystem = '*S-1-5-18'; $SidLocalService = '*S-1-5-19'

function Info($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "Warning: $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "Error: $msg" -ForegroundColor Red; exit 1 }
function Confirm-Step($question) {
    if ($Yes) { return $true }
    $reply = Read-Host "$question [Y/n]"
    return ($reply -eq '' -or $reply -match '^[Yy]')
}
function Get-AccountName($sid) {
    ([System.Security.Principal.SecurityIdentifier]$sid.TrimStart('*')).Translate([System.Security.Principal.NTAccount]).Value
}
function Invoke-Native {
    param([string]$File, [string[]]$Arguments)
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { Fail "'$File $($Arguments -join ' ')' failed with exit code $LASTEXITCODE" }
}
function Stop-Bot {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
    # The task runs cmd.exe -> python.exe; make sure the bot process itself is gone too.
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='cmd.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$InstallDir*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
function Confirm-No($question) {
    # Default no, and no when not interactive.
    if ($Yes) { return $false }
    $reply = Read-Host "$question [y/N]"
    return ($reply -match '^[Yy]')
}

# ------------------------------------------------------------------ automatic updates (opt-in)
# A scheduled task runs auto-update.ps1 daily as SYSTEM. The repository is public, so it needs no GitHub token; an
# optional one (UPDATE_GITHUB_TOKEN, or stored by an earlier version) is kept in updater\github-token, separate from
# .env and readable by Administrators and SYSTEM only.
function Test-AutoUpdate {
    return [bool](Get-ScheduledTask -TaskName $UpdateTaskName -ErrorAction SilentlyContinue)
}
function Protect-UpdaterDir {
    New-Item -ItemType Directory -Force -Path $UpdaterDir | Out-Null
    # No inherited access: the bot's account (LOCAL SERVICE) and normal users must not read the token.
    Invoke-Native icacls @($UpdaterDir, '/inheritance:r', '/grant:r', "${SidAdmins}:(OI)(CI)F", "${SidSystem}:(OI)(CI)F", '/Q') | Out-Null
    if (Test-Path $TokenFile) {
        Invoke-Native icacls @($TokenFile, '/inheritance:r', '/grant:r', "${SidAdmins}:F", "${SidSystem}:F", '/Q') | Out-Null
    }
}
function Save-UpdateToken {
    # Optional: without a token the updater downloads anonymously (the repository is public).
    $token = $env:UPDATE_GITHUB_TOKEN
    if (-not $token -and (Test-Path $TokenFile)) {
        if ($Yes -or -not (Confirm-No 'Replace or delete the stored GitHub token (not needed: the repository is public)?')) {
            Info 'Keeping the stored GitHub token'
            return
        }
        $s = Read-Host -AsSecureString 'New GitHub token (input is hidden; Enter to delete the stored one)'
        $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
        try { $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
        if (-not "$token".Trim()) {
            Remove-Item -Force $TokenFile
            Info 'Deleted the stored GitHub token; updates are downloaded without one'
            return
        }
    }
    $token = "$token".Trim()
    if (-not $token) {
        Info 'No GitHub token needed: the repository is public (for a private fork, set UPDATE_GITHUB_TOKEN)'
        return
    }
    if ($token -notmatch '^[A-Za-z0-9_]+$') { Fail 'That does not look like a GitHub token (letters, digits and _ only).' }
    Protect-UpdaterDir
    [IO.File]::WriteAllText($TokenFile, $token)
    Protect-UpdaterDir
    Info "Saved the GitHub token in $TokenFile (readable by Administrators and SYSTEM only)"
}
function Register-UpdateTask {
    $script = Join-Path $InstallDir 'auto-update.ps1'
    $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $updateAction = New-ScheduledTaskAction -Execute $ps -WorkingDirectory $env:SystemRoot `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$script`""
    $updateTrigger = New-ScheduledTaskTrigger -Daily -At '04:00' -RandomDelay (New-TimeSpan -Hours 4)
    $updatePrincipal = New-ScheduledTaskPrincipal -UserId (Get-AccountName $SidSystem) -LogonType ServiceAccount -RunLevel Highest
    $updateSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $UpdateTaskName -Action $updateAction -Trigger $updateTrigger -Principal $updatePrincipal `
        -Settings $updateSettings -Description 'TwitchDiscordBot - installs new releases (automatic updates)' -Force | Out-Null
}
function Enable-AutoUpdate {
    if (-not (Test-Path (Join-Path $InstallDir 'auto-update.ps1'))) {
        Fail "$InstallDir has no auto-update.ps1; install this release first (install.cmd -AutoUpdate)."
    }
    Save-UpdateToken
    $installedUpdater = Join-Path $InstallDir 'twitchbot\updater.py'
    $tokenOptional = (Test-Path $installedUpdater) -and
        (Select-String -Quiet -Path $installedUpdater -Pattern '^TOKEN_OPTIONAL = True$')
    if (-not (Test-Path $TokenFile) -and -not $tokenOptional) {
        # The installed version's updater (releases before this change) still needs a token.
        Fail "The version installed in $InstallDir needs a GitHub token for automatic updates; install this release first (install.cmd -AutoUpdate)."
    }
    Protect-UpdaterDir
    Register-UpdateTask
    Info "Automatic updates are on: a newer release is installed daily (log: $UpdaterDir\update.log)."
}
function Disable-AutoUpdate {
    Unregister-ScheduledTask -TaskName $UpdateTaskName -Confirm:$false -ErrorAction SilentlyContinue
    if (Test-Path $UpdaterDir) { Remove-Item -Recurse -Force $UpdaterDir }
}

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail 'Please run as administrator (double-click install.cmd, or use an elevated PowerShell).'
}

# ------------------------------------------------------------------ stop / remove the task only
if ($StopService -or $RemoveService) {
    Info 'Stopping the bot'
    Stop-Bot
    if ($RemoveService) {
        Info 'Removing the scheduled tasks'
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $UpdateTaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    exit 0
}

# ------------------------------------------------------------------ turn automatic updates on / off only
if ($DisableAutoUpdate) {
    Disable-AutoUpdate
    Info 'Automatic updates are off (a stored GitHub token was deleted).'
    exit 0
}
if ($EnableAutoUpdate) {
    if (-not (Test-Path $VenvPy)) { Fail "TwitchDiscordBot is not installed in $InstallDir." }
    Enable-AutoUpdate
    exit 0
}

# ------------------------------------------------------------------ uninstall
if ($Uninstall) {
    Info 'Stopping the bot'
    Stop-Bot
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $UpdateTaskName -Confirm:$false -ErrorAction SilentlyContinue
    if (Test-Path $InstallDir) {
        if ($Purge) {
            Info "Deleting $InstallDir (including settings, data and logs)"
            Remove-Item -Recurse -Force $InstallDir
        } else {
            Info "Removing program files from $InstallDir (keeping .env, data and logs; a stored GitHub token is deleted)"
            Get-ChildItem -Force $InstallDir | Where-Object { $_.Name -notin @('.env', 'data', 'logs') } |
                Remove-Item -Recurse -Force
        }
    }
    Info 'Uninstalled.'
    exit 0
}

# ------------------------------------------------------------------ python
function Test-Python($exe, [string[]]$pre) {
    try {
        $out = & $exe @pre -c "import sys, venv; print(sys.executable); print(sys.base_prefix); sys.exit(sys.version_info < (3, 10))" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) {
            $lines = @($out)
            # Skip the Microsoft Store stub/sandboxed Python: a service account cannot use it.
            if ($lines[0] -notlike '*\WindowsApps\*') { return @{ Exe = $lines[0]; Base = $lines[1] } }
        }
    } catch {
        Write-Verbose "Python candidate $exe failed: $_"
    }
    return $null
}
function Find-Python {
    if ($env:PYTHON -and (Test-Path $env:PYTHON)) { $r = Test-Python $env:PYTHON @(); if ($r) { return $r } }
    foreach ($v in '3.14', '3.13', '3.12', '3.11', '3.10') {
        if (Get-Command py -ErrorAction SilentlyContinue) { $r = Test-Python 'py' @("-$v"); if ($r) { return $r } }
    }
    foreach ($exe in @('python') + (Get-ChildItem "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | ForEach-Object FullName)) {
        if ($exe -and (Get-Command $exe -ErrorAction SilentlyContinue)) { $r = Test-Python $exe @(); if ($r) { return $r } }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail 'Python 3.10+ is required. Install it for all users from https://www.python.org/downloads/ and run the installer again.'
    }
    if (-not (Confirm-Step 'Python 3.10+ is required. Install Python 3.14 for all users with winget now?')) {
        Fail 'Python 3.10+ is required.'
    }
    Info 'Installing Python 3.14 (this can take a few minutes)'
    & winget install --exact --id Python.Python.3.14 --scope machine --silent --accept-package-agreements --accept-source-agreements
    $python = Find-Python
    if (-not $python) { Fail 'Python was installed but could not be found. Open a new window and run the installer again.' }
}
Info "Using Python at $($python.Exe)"

# The bot runs as LOCAL SERVICE, which cannot read Python installed inside a user profile.
$runAs = $SidLocalService
if ($python.Base -like "$env:SystemDrive\Users\*") {
    Warn "Python is installed inside a user profile ($($python.Base)). The bot will run as SYSTEM instead of LOCAL SERVICE. Install Python 'for all users' to avoid this."
    $runAs = $SidSystem
}

# ------------------------------------------------------------------ files
if (-not $Reconfigure) {
    if (-not ((Test-Path (Join-Path $SrcDir 'Main.py')) -and (Test-Path (Join-Path $SrcDir 'twitchbot')))) {
        Fail 'Run this installer from the extracted release folder.'
    }
    # Ask once, on a fresh interactive install; upgrades keep whatever was chosen before.
    if (-not ($AutoUpdate -or $NoAutoUpdate) -and -not (Test-Path (Join-Path $InstallDir 'Main.py')) -and
        (Confirm-No 'Install new releases automatically (daily)?')) {
        $AutoUpdate = $true
    }
    Stop-Bot

    # Import settings from an earlier install made from a git checkout (scripts\install-startup.ps1).
    $oldTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    if ($oldTask -and -not (Test-Path $EnvFile)) {
        $oldDir = $oldTask.Actions[0].WorkingDirectory
        if ($oldDir -and ($oldDir.TrimEnd('\') -ne $InstallDir) -and (Test-Path (Join-Path $oldDir '.env')) -and
            (Confirm-Step "Found an existing installation in $oldDir. Import its settings and data?")) {
            Info "Importing settings and data from $oldDir"
            Copy-Item (Join-Path $oldDir '.env') $EnvFile
            if (Test-Path (Join-Path $oldDir 'data')) { Copy-Item -Recurse -Force (Join-Path $oldDir 'data') $InstallDir }
            Get-ChildItem $oldDir -File | Where-Object { $_.Name -match '^(Streams\.json|\d+-Stream\.json)$' } |
                Copy-Item -Destination $InstallDir
        }
    }

    if ($SrcDir.TrimEnd('\') -ne $InstallDir) {
        $version = if (Test-Path (Join-Path $SrcDir 'VERSION')) { (Get-Content (Join-Path $SrcDir 'VERSION')).Trim() } else { '' }
        Info "Installing TwitchDiscordBot $version into $InstallDir"
        $code = Join-Path $InstallDir 'twitchbot'
        if (Test-Path $code) { Remove-Item -Recurse -Force $code }
        Copy-Item -Recurse (Join-Path $SrcDir 'twitchbot') $InstallDir
        foreach ($f in 'Main.py', 'requirements.txt', 'VERSION', 'CHANGES.md', '.env.example', 'start-bot.cmd', 'install.ps1',
                       'install.cmd', 'uninstall.cmd', 'auto-update.ps1', 'README.md', 'LICENSE') {
            $p = Join-Path $SrcDir $f
            if (Test-Path $p) { Copy-Item -Force $p $InstallDir }
        }
    }
    foreach ($d in 'data', 'logs') { New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir $d) | Out-Null }

    # Only admins, SYSTEM and the bot's account get access (by default ProgramData lets any user create files,
    # and new files like .env would be readable by everyone).
    Invoke-Native icacls @($InstallDir, '/inheritance:r', '/grant:r', "${SidAdmins}:(OI)(CI)F", "${SidSystem}:(OI)(CI)F",
        "${SidLocalService}:(OI)(CI)RX", '/Q') | Out-Null
    foreach ($d in 'data', 'logs') {
        Invoke-Native icacls @((Join-Path $InstallDir $d), '/grant', "${SidLocalService}:(OI)(CI)M", '/Q') | Out-Null
    }

    if ((Test-Path $VenvPy) -and -not (Test-Python $VenvPy @())) {
        Info 'Existing virtual environment is broken or too old; recreating it'
        Remove-Item -Recurse -Force (Join-Path $InstallDir '.venv')
    }
    if (-not (Test-Path $VenvPy)) {
        Info 'Creating virtual environment'
        Invoke-Native $python.Exe @('-m', 'venv', (Join-Path $InstallDir '.venv'))
    }
    Info 'Installing Python packages'
    Invoke-Native $VenvPy @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '-q', '-r',
        (Join-Path $InstallDir 'requirements.txt'))
    # Same marker start-bot.cmd uses, so it doesn't try to reinstall packages (it can't write here as LOCAL SERVICE).
    Copy-Item -Force (Join-Path $InstallDir 'requirements.txt') (Join-Path $InstallDir '.venv\requirements.installed.txt')
    & $VenvPy -m compileall -q (Join-Path $InstallDir 'twitchbot') | Out-Null
}
if (-not (Test-Path $VenvPy)) { Fail "TwitchDiscordBot is not installed in $InstallDir; run install.cmd from the release folder first." }

# ------------------------------------------------------------------ configuration
function Test-EnvKey([string[]]$keys) {
    if (-not (Test-Path $EnvFile)) { return $false }
    $text = Get-Content -Raw $EnvFile
    foreach ($k in $keys) { if ($text -match "(?m)^\s*$([regex]::Escape($k))\s*=\s*['`"]?[^'`"\s]") { return $true } }
    return $false
}
function Read-Value($name, $prompt, [bool]$secret, [bool]$required) {
    if ($Yes) {
        $v = [Environment]::GetEnvironmentVariable($name)
        if ($required -and -not $v) { Fail "$name must be set in the environment when using -Yes." }
        return $v
    }
    while ($true) {
        if ($secret) {
            $s = Read-Host -AsSecureString $prompt
            $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
            try { $v = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
        } else {
            $v = Read-Host $prompt
        }
        $v = "$v".Trim()
        if ($v -or -not $required) { return $v }
        Write-Host '  A value is required.'
    }
}
function Set-Configuration {
    Write-Host ''
    Write-Host 'Credentials (input for secrets is hidden):'
    Write-Host '  * Discord bot token:  https://discord.com/developers/applications -> your app -> Bot -> Reset Token'
    Write-Host '    Recommended: on the same Bot page turn on "Server Members Intent" (Privileged Gateway Intents) so update'
    Write-Host '    notices reach server admins too; without it they go to server owners only.'
    Write-Host '  * Twitch Client ID and secret: https://dev.twitch.tv/console/apps -> your app -> Manage'
    $values = [ordered]@{
        DISCORD_TOKEN     = Read-Value 'DISCORD_TOKEN' 'Discord bot token' $true $true
        TWITCH_APP_ID     = Read-Value 'TWITCH_APP_ID' 'Twitch Client ID' $false $true
        TWITCH_APP_SECRET = Read-Value 'TWITCH_APP_SECRET' 'Twitch Client secret' $true $true
        OWNER_ID          = Read-Value 'OWNER_ID' 'Your Discord user ID for /restart (optional, Enter to skip)' $false $false
    }
    if (-not (Test-Path $EnvFile)) { New-Item -ItemType File -Path $EnvFile | Out-Null }
    # python-dotenv's set_key quotes values safely and keeps other settings already in the file.
    foreach ($k in $values.Keys) { [Environment]::SetEnvironmentVariable("NEW_$k", $values[$k], 'Process') }
    $env:ENV_FILE = $EnvFile
    $script = @'
import os
from dotenv import set_key
for key in ("DISCORD_TOKEN", "TWITCH_APP_ID", "TWITCH_APP_SECRET", "OWNER_ID"):
    value = os.environ.get("NEW_" + key, "")
    if value:
        set_key(os.environ["ENV_FILE"], key, value, quote_mode="always")
'@
    $script | & $VenvPy -
    $code = $LASTEXITCODE
    foreach ($k in $values.Keys) { [Environment]::SetEnvironmentVariable("NEW_$k", $null, 'Process') }
    if ($code -ne 0) { Fail 'Could not write the settings file.' }
    Protect-EnvFile  # set_key replaces the file, so (re)apply the permissions afterwards
}
function Protect-EnvFile {
    # Secrets: only admins, SYSTEM and the bot's account may read .env.
    Invoke-Native icacls @($EnvFile, '/inheritance:r', '/grant:r', "${SidAdmins}:F", "${SidSystem}:F", "${runAs}:R", '/Q') | Out-Null
}

if ($Reconfigure -or -not (Test-EnvKey @('DISCORD_TOKEN', 'TOKEN')) -or -not (Test-EnvKey @('TWITCH_APP_SECRET', 'TWITCH-APP-SECRET'))) {
    Set-Configuration
} else {
    Info "Keeping existing settings in $EnvFile (use -Reconfigure to change them)"
    Protect-EnvFile
}

if (-not $SkipCheck) {
    while ($true) {
        Info 'Checking credentials with Discord and Twitch'
        Push-Location $InstallDir
        try { & $VenvPy Main.py --check; $ok = ($LASTEXITCODE -eq 0) } finally { Pop-Location }
        if ($ok) { break }
        if ($Yes) { Fail 'Credential check failed (use -SkipCheck to install anyway).' }
        if (Confirm-Step 'Enter the credentials again?') { Set-Configuration }
        elseif (Confirm-Step 'Continue anyway?') { break }
        else { Fail 'Aborted.' }
    }
}

# A new version's slash commands only show up in Discord once they are registered; the bot also does this at
# every start (with retries), so a failure here is not fatal.
Info 'Registering the slash commands with Discord'
$registered = $false
Push-Location $InstallDir
try { & $VenvPy Main.py --sync-commands; $registered = ($LASTEXITCODE -eq 0) } catch { $registered = $false } finally { Pop-Location }
if (-not $registered) { Warn 'Could not register the slash commands now; the bot registers them again every time it starts.' }

# ------------------------------------------------------------------ automatic updates
if ($AutoUpdate) {
    Enable-AutoUpdate
} elseif ($NoAutoUpdate) {
    if ((Test-AutoUpdate) -or (Test-Path $UpdaterDir)) {
        Disable-AutoUpdate
        Info 'Automatic updates are off (a stored GitHub token was deleted).'
    }
} elseif (Test-AutoUpdate) {
    Protect-UpdaterDir
    Register-UpdateTask  # refresh the task; the choice and a stored token stay as they are
    Info "Keeping automatic updates on (turn off: $InstallDir\install.cmd -DisableAutoUpdate)"
}

# ------------------------------------------------------------------ service
if ($NoService) {
    Info "Done. Start the bot with: `"$InstallDir\start-bot.cmd`" --watch"
    exit 0
}

Info "Registering scheduled task '$TaskName'"
Stop-Bot
$startScript = Join-Path $InstallDir 'start-bot.cmd'
$consoleLog = Join-Path $InstallDir 'logs\console.log'
$action = New-ScheduledTaskAction -Execute 'cmd.exe' `
    -Argument "/c `"`"$startScript`" --watch >> `"$consoleLog`" 2>&1`"" -WorkingDirectory $InstallDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = 'PT30S'
$taskPrincipal = New-ScheduledTaskPrincipal -UserId (Get-AccountName $runAs) -LogonType ServiceAccount -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $taskPrincipal `
    -Settings $settings -Description 'TwitchDiscordBot - Twitch live notifications for Discord' -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 10
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*Main.py*" -and $_.ExecutablePath -like "$InstallDir*" }
if ($running) {
    Info "TwitchDiscordBot is running as $(Get-AccountName $runAs) and will start at boot."
} else {
    Warn 'The bot does not appear to be running. Recent log output:'
    foreach ($log in (Join-Path $InstallDir 'logs\bot.log'), $consoleLog) {
        if (Test-Path $log) { Get-Content $log -Tail 20 }
    }
    exit 1
}

Write-Host @"

  Logs:          $InstallDir\logs\bot.log
  Restart:       Restart from Task Scheduler, or: Stop-ScheduledTask $TaskName; Start-ScheduledTask $TaskName
  Change creds:  $InstallDir\install.cmd -Reconfigure
  Upgrade:       run install.cmd from a newer release folder
  Uninstall:     $InstallDir\uninstall.cmd   (add -Purge to delete settings and data)
  Auto-update:   $InstallDir\install.cmd -EnableAutoUpdate   or   -DisableAutoUpdate

Next, in Discord: /setup channel, then /stream add <twitch name>.
"@
