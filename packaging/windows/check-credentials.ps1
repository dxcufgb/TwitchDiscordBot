<#
.SYNOPSIS
    Checks Discord/Twitch credentials for the setup wizard, without needing Python.
    Values come from the environment (TDB_DISCORD_TOKEN, TDB_TWITCH_ID, TDB_TWITCH_SECRET) so they
    never appear on a command line. TDB_UPDATE_NOTIFY is the UPDATE_NOTIFY setting from .env (empty = admins, the
    default); only then is the Server Members Intent checked. A short, secret-free report is written to -OutFile.
    Exit code 0 = both accepted, 1 = something was rejected or could not be checked,
    2 = both accepted with a warning (the Server Members Intent is off: update notices reach server owners only).
#>
param([Parameter(Mandatory = $true)][string]$OutFile)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$report = New-Object System.Collections.Generic.List[string]
$ok = $true
$warning = $false
$notifyMode = "$env:TDB_UPDATE_NOTIFY".Trim().ToLowerInvariant()
if (-not $notifyMode) { $notifyMode = 'admins' }

function Get-StatusCode($err) {
    try { return [int]$err.Exception.Response.StatusCode } catch { return 0 }
}

try {
    $me = Invoke-RestMethod -Uri 'https://discord.com/api/v10/users/@me' -TimeoutSec 20 `
        -Headers @{ Authorization = "Bot $env:TDB_DISCORD_TOKEN" }
    $report.Add("Discord: OK (bot account $($me.username))")
    # With UPDATE_NOTIFY=admins the bot asks for the privileged Server Members Intent (application flags
    # GATEWAY_GUILD_MEMBERS[_LIMITED]) to find server admins. Without it the bot still runs (owner-only notices).
    if ($notifyMode -eq 'admins') {
        try {
            $app = Invoke-RestMethod -Uri 'https://discord.com/api/v10/applications/@me' -TimeoutSec 20 `
                -Headers @{ Authorization = "Bot $env:TDB_DISCORD_TOKEN" }
            if (([long]$app.flags -band ((1 -shl 14) -bor (1 -shl 15))) -eq 0) {
                $warning = $true
                $report.Add('Warning: the "Server Members Intent" is off, so update notices go to server owners only. To reach server admins too, turn it on: Developer Portal -> your app -> Bot -> Privileged Gateway Intents. Or set UPDATE_NOTIFY=owner in .env.')
            }
        } catch {
            $report.Add('Discord: could not check the Server Members Intent.')
        }
    }
} catch {
    $ok = $false
    if ((Get-StatusCode $_) -eq 401) { $report.Add('Discord: the bot token was rejected.') }
    else { $report.Add('Discord: could not reach Discord to check the token.') }
}

try {
    Invoke-RestMethod -Method Post -Uri 'https://id.twitch.tv/oauth2/token' -TimeoutSec 20 -Body @{
        client_id = $env:TDB_TWITCH_ID; client_secret = $env:TDB_TWITCH_SECRET; grant_type = 'client_credentials'
    } | Out-Null
    $report.Add('Twitch: OK')
} catch {
    $ok = $false
    $code = Get-StatusCode $_
    if ($code -eq 400 -or $code -eq 401 -or $code -eq 403) { $report.Add('Twitch: the Client ID or Client secret was rejected.') }
    else { $report.Add('Twitch: could not reach Twitch to check the Client ID and secret.') }
}

Set-Content -Path $OutFile -Value $report -Encoding ASCII
if (-not $ok) { exit 1 }
if ($warning) { exit 2 }
exit 0
