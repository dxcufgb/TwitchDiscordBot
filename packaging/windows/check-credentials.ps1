<#
.SYNOPSIS
    Checks Discord/Twitch credentials for the setup wizard, without needing Python.
    Values come from the environment (TDB_DISCORD_TOKEN, TDB_TWITCH_ID, TDB_TWITCH_SECRET) so they
    never appear on a command line. A short, secret-free report is written to -OutFile.
    Exit code 0 = both accepted, 1 = something was rejected or could not be checked.
#>
param([Parameter(Mandatory = $true)][string]$OutFile)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$report = New-Object System.Collections.Generic.List[string]
$ok = $true

function Get-StatusCode($err) {
    try { return [int]$err.Exception.Response.StatusCode } catch { return 0 }
}

try {
    $me = Invoke-RestMethod -Uri 'https://discord.com/api/v10/users/@me' -TimeoutSec 20 `
        -Headers @{ Authorization = "Bot $env:TDB_DISCORD_TOKEN" }
    $report.Add("Discord: OK (bot account $($me.username))")
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
if ($ok) { exit 0 } else { exit 1 }
