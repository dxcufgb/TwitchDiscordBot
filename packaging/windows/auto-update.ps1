<#
.SYNOPSIS
    Installs new TwitchDiscordBot releases (opt-in automatic updates).

.DESCRIPTION
    Installed as C:\ProgramData\TwitchDiscordBot\auto-update.ps1 and run daily as SYSTEM by the
    'TwitchDiscordBot Update' scheduled task. Run it by hand from an elevated PowerShell:
        powershell -ExecutionPolicy Bypass -File C:\ProgramData\TwitchDiscordBot\auto-update.ps1          update now
        powershell -ExecutionPolicy Bypass -File C:\ProgramData\TwitchDiscordBot\auto-update.ps1 -Check   only report

    It downloads the newest release's -windows.zip, verifies it against SHA256SUMS.txt and runs that release's
    install.ps1 (the normal upgrade path, which keeps .env, data and logs). If the upgrade fails, the previous
    program files are restored and the bot is started again. Log: <install folder>\updater\update.log

.PARAMETER Check  Only report whether a newer release exists.
#>
[CmdletBinding()]
param(
    [switch]$Check
)
$ErrorActionPreference = 'Stop'
$InstallDir = $PSScriptRoot.TrimEnd('\')
$TaskName = 'TwitchDiscordBot'
$UpdaterDir = Join-Path $InstallDir 'updater'
$TokenFile = Join-Path $UpdaterDir 'github-token'
$LogFile = Join-Path $UpdaterDir 'update.log'
$VenvPy = Join-Path $InstallDir '.venv\Scripts\python.exe'
$Keep = @('.env', 'data', 'logs', 'updater')

function Write-UpdateLog([string]$Message) {
    $line = '{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $Message
    Write-Host $line  # not Write-Output: that would become part of the calling function's return value
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Stop-Bot {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='cmd.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$InstallDir*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Get-ProgramItem {
    Get-ChildItem -Force $InstallDir | Where-Object { $Keep -notcontains $_.Name }
}

function Invoke-Updater([string[]]$Arguments) {
    # Isolated mode (-I): only the standard library and this script, nothing from the working directory.
    $updater = Join-Path $InstallDir 'twitchbot\updater.py'
    $all = @('-I', $updater, '--install-dir', $InstallDir, '--token-file', $TokenFile, '--platform', 'windows',
        '--log-file', $LogFile) + $Arguments
    # Windows PowerShell 5.1 turns a native program's stderr into error records; with 'Stop' those would abort.
    $ErrorActionPreference = 'Continue'
    Push-Location $env:SystemRoot
    try {
        $out = & $VenvPy @all
        return @{ Code = $LASTEXITCODE; Output = @($out) }
    } finally {
        Pop-Location
    }
}

function Update-Bot([bool]$CheckOnly) {
    $result = Invoke-Updater @('check')
    if ($CheckOnly) {
        if ($result.Code -eq 3) { return 0 }
        return $result.Code
    }
    if ($result.Code -eq 3) { return 0 }
    if ($result.Code -ne 0) { Write-UpdateLog 'Update check failed; the installed version keeps running.'; return 1 }

    # Work inside the updater folder: only Administrators and SYSTEM can write there (unlike %TEMP% of SYSTEM).
    $work = Join-Path $UpdaterDir ('work-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $work | Out-Null
    try {
        $result = Invoke-Updater @('download', '--dest', (Join-Path $work 'new'))
        if ($result.Code -eq 3) { return 0 }
        if ($result.Code -ne 0 -or -not $result.Output) {
            Write-UpdateLog 'Download failed; the installed version keeps running.'
            return 1
        }
        $newDir = [string]$result.Output[-1]
        $installer = Join-Path $newDir 'install.ps1'
        if (-not $newDir.StartsWith((Join-Path $work 'new'), [StringComparison]::OrdinalIgnoreCase) -or
            -not (Test-Path $installer)) {
            Write-UpdateLog 'The downloaded release has no install.ps1.'
            return 1
        }
        $newVersion = (Get-Content (Join-Path $newDir 'VERSION') -ErrorAction SilentlyContinue | Select-Object -First 1)

        Write-UpdateLog 'Backing up the current program files'
        $backup = Join-Path $work 'backup'
        New-Item -ItemType Directory -Path $backup | Out-Null
        Get-ProgramItem | ForEach-Object { Copy-Item -Recurse -Force $_.FullName $backup }

        # Release notes for the bot, which DMs them to server admins when the new version starts. Written before the
        # installer restarts the bot: to a new, unique file (data\ is writable by the bot's account), then renamed.
        $notice = Join-Path $work 'new\update-notice.json'
        $noticeDest = Join-Path $InstallDir 'data\update-notice.json'
        if (Test-Path $notice) {
            try {
                $tmp = Join-Path $InstallDir ('data\.update-notice-' + [Guid]::NewGuid().ToString('N') + '.tmp')
                $bytes = [IO.File]::ReadAllBytes($notice)
                $stream = [IO.File]::Open($tmp, 'CreateNew', 'Write', 'None')
                try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
                if (Test-Path $noticeDest) { [IO.File]::Delete($noticeDest) }
                [IO.File]::Move($tmp, $noticeDest)
            } catch {
                Write-UpdateLog "Could not save the release notes for the update message: $($_.Exception.Message)"
            }
        }

        Write-UpdateLog "Installing TwitchDiscordBot $newVersion"
        # The release's installer upgrades in place, keeps settings, data, this auto-update setting and the token,
        # and exits non-zero if the bot does not stay up.
        $installArgs = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', "`"$installer`"",
            '-InstallDir', "`"$InstallDir`"", '-Yes', '-SkipCheck')
        if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) { $installArgs += '-NoService' }
        foreach ($name in 'DISCORD_TOKEN', 'TWITCH_APP_ID', 'TWITCH_APP_SECRET', 'OWNER_ID', 'UPDATE_GITHUB_TOKEN') {
            [Environment]::SetEnvironmentVariable($name, $null, 'Process')
        }
        # install.ps1 needs the Python the virtual environment was made from (SYSTEM may not find a per-user one).
        $cfg = Join-Path $InstallDir '.venv\pyvenv.cfg'
        if (Test-Path $cfg) {
            $homeLine = Select-String -Path $cfg -Pattern '^\s*home\s*=\s*(.+?)\s*$' | Select-Object -First 1
            if ($homeLine) {
                $basePy = Join-Path $homeLine.Matches[0].Groups[1].Value 'python.exe'
                if (Test-Path $basePy) { $env:PYTHON = $basePy }
            }
        }
        $stdout = Join-Path $work 'install.out'
        $stderr = Join-Path $work 'install.err'
        $p = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
            -ArgumentList $installArgs -WorkingDirectory $env:SystemRoot -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        foreach ($f in $stdout, $stderr) {
            if (Test-Path $f) { Get-Content $f | Add-Content -Path $LogFile -Encoding UTF8 }
        }
        if ($p.ExitCode -eq 0) {
            $key = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{B7E3F0A4-5C2D-4E8B-9F1A-6D3C2B7A8E51}_is1'
            if ($newVersion -and (Test-Path $key)) {
                # Keep the version shown in Apps & features (setup wizard installs) current.
                Set-ItemProperty -Path $key -Name DisplayVersion -Value $newVersion
            }
            Write-UpdateLog "Updated to TwitchDiscordBot $newVersion."
            return 0
        }

        Write-UpdateLog "The upgrade failed (exit code $($p.ExitCode)); restoring the previous version"
        Stop-Bot
        Remove-Item -Force $noticeDest -ErrorAction SilentlyContinue
        Get-ProgramItem | Remove-Item -Recurse -Force
        Get-ChildItem -Force $backup | ForEach-Object { Copy-Item -Recurse -Force $_.FullName $InstallDir }
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { Start-ScheduledTask -TaskName $TaskName }
        Write-UpdateLog 'Rolled back; the previous version is running again.'
        return 1
    } finally {
        Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
    }
}

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'Error: please run as administrator.'
    exit 1
}
if (-not (Test-Path $TokenFile)) {
    Write-Host "Error: no GitHub token in $TokenFile. Turn automatic updates on with: $InstallDir\install.cmd -EnableAutoUpdate"
    exit 1
}
if (-not (Test-Path $VenvPy)) {
    Write-Host "Error: TwitchDiscordBot is not installed in $InstallDir."
    exit 1
}

# One run at a time: the lock file stays open (and exclusive) until this script ends.
try {
    $lock = [IO.File]::Open((Join-Path $UpdaterDir 'update.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
} catch {
    $inner = $_.Exception
    while ($inner.InnerException) { $inner = $inner.InnerException }
    if ($inner -isnot [IO.IOException]) { throw }
    Write-Host 'Another update is already running; nothing to do.'
    exit 0
}
try {
    $code = Update-Bot -CheckOnly $Check.IsPresent
} catch {
    Write-UpdateLog "Update failed: $($_.Exception.Message)"
    $code = 1
} finally {
    $lock.Dispose()
}
exit $code
