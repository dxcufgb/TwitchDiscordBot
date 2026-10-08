# TwitchDiscordBot

Posts an alert in Discord, and pings a per-stream role, when the Twitch channels you track go live.

## Before you install

1. **Discord bot** ([Developer Portal](https://discord.com/developers/applications)): create an application,
   add a bot, and copy its **token**. No privileged intents are needed. Invite it to your server with the
   `bot` and `applications.commands` scopes and these permissions: *View Channels, Send Messages, Embed Links,
   Mention @everyone/@here/All Roles, Manage Roles*.
2. **Twitch app** ([Twitch dev console](https://dev.twitch.tv/console/apps)): register an application
   (OAuth redirect `http://localhost`, category *Chat Bot*) and copy its **Client ID** and **Client secret**.

The installer asks for these values and checks them with Discord and Twitch before starting the bot.

## Linux (systemd)

```bash
sudo ./install.sh
```

The installer:

- installs Python 3.10+ with your package manager if needed;
- creates a `twitchbot` system user;
- installs the bot into `/opt/twitchdiscordbot`;
- starts the `twitchdiscordbot` service, which also starts at boot.

| Task | Command |
|---|---|
| Status / logs | `systemctl status twitchdiscordbot` · `journalctl -u twitchdiscordbot -f` |
| Restart | `sudo systemctl restart twitchdiscordbot` |
| Change credentials | `sudo /opt/twitchdiscordbot/install.sh --reconfigure` |
| Upgrade | run `sudo ./install.sh` from the newer release folder |
| Uninstall | `sudo /opt/twitchdiscordbot/install.sh --uninstall` (add `--purge` to delete settings and data) |

Unattended install: `sudo DISCORD_TOKEN=… TWITCH_APP_ID=… TWITCH_APP_SECRET=… ./install.sh --yes`.
Other options: `--prefix DIR`, `--user NAME`, `--no-service`, `--skip-check`, `--auto-update` (see
`./install.sh --help` and [Automatic updates](#automatic-updates)).

## Windows 10/11 or Windows Server

**Setup wizard (recommended):** run `twitchdiscordbot-<version>-windows-setup.exe` from the release page. It
asks for the credentials, checks them, and installs everything listed below. Uninstall it from
*Settings -> Apps -> Installed apps*; you can choose there whether to keep your settings. The setup isn't
code-signed yet, so Windows SmartScreen may warn about it: click *More info* -> *Run anyway*. For a silent
install, set `DISCORD_TOKEN`, `TWITCH_APP_ID` and `TWITCH_APP_SECRET` in the environment and run
`setup.exe /VERYSILENT /SUPPRESSMSGBOXES`.

**From the zip:** double-click **`install.cmd`** and approve the administrator prompt. Either way, the installer:

- installs Python 3.14 with winget if needed;
- installs the bot into `C:\ProgramData\TwitchDiscordBot`;
- registers the **TwitchDiscordBot** scheduled task. The task runs at boot as *LOCAL SERVICE* and restarts the bot if it stops.

| Task | How |
|---|---|
| Logs | `C:\ProgramData\TwitchDiscordBot\logs\bot.log` |
| Restart | Task Scheduler → TwitchDiscordBot → End, then Run |
| Change credentials | Run the setup wizard again, or `C:\ProgramData\TwitchDiscordBot\install.cmd -Reconfigure` |
| Upgrade | Run the newer setup wizard, or `install.cmd` from the newer release folder |
| Uninstall | *Apps & features* (setup wizard), or `C:\ProgramData\TwitchDiscordBot\uninstall.cmd` (zip; add `-Purge` to delete settings and data) |

## Automatic updates

Off by default. When turned on, the bot's computer checks GitHub once a day for a newer release and installs
it with the same installer you would run by hand: settings (`.env`), data and logs are kept with their
permissions, and the bot is restarted. Pre-releases and drafts are ignored, and older or equal versions are
never installed. The download is checked against the release's `SHA256SUMS.txt`. If the upgrade fails (for
example the new version does not stay running), the previous program files are restored and the old version
is started again.

**GitHub token.** The repository is private, so the updater needs a token. Create a *fine-grained personal
access token* at <https://github.com/settings/personal-access-tokens>: *Resource owner* `dxcufgb`, *Only select
repositories* -> `TwitchDiscordBot`, *Repository permissions* -> **Contents: Read-only**, nothing else. Give it
an expiry date you will remember: when it expires, updates stop (the log says the token was rejected) until you
store a new one with the "turn on" command below. The token is stored separately from the bot's settings, readable only by
root (Linux, `/opt/twitchdiscordbot/updater/github-token`, mode 600) or by Administrators and SYSTEM (Windows,
`C:\ProgramData\TwitchDiscordBot\updater\github-token`). It is only sent to `api.github.com` and never logged.

**Linux** (systemd timer `twitchdiscordbot-update.timer`, daily with a random delay of up to 4 hours, runs as root):

| Task | Command |
|---|---|
| Turn on while installing | `sudo ./install.sh --auto-update` (a fresh interactive install also asks) |
| Turn on later / replace the token | `sudo /opt/twitchdiscordbot/install.sh --enable-auto-update` |
| Turn off (deletes the token) | `sudo /opt/twitchdiscordbot/install.sh --disable-auto-update` |
| Check without installing | `sudo /opt/twitchdiscordbot/auto-update.sh --check` |
| Update now | `sudo /opt/twitchdiscordbot/auto-update.sh` (or `sudo systemctl start twitchdiscordbot-update`) |
| Log | `journalctl -u twitchdiscordbot-update` |

The token is asked for with hidden input. For `--yes`, pass it in the environment instead:
`sudo UPDATE_GITHUB_TOKEN=… ./install.sh --yes --auto-update`.

**Windows** (scheduled task *TwitchDiscordBot Update*, daily around 04:00 with a random delay of up to 4 hours,
runs as SYSTEM):

| Task | How |
|---|---|
| Turn on while installing | Setup wizard: tick *Install new releases automatically* and enter the token. Zip: `install.cmd -AutoUpdate` (a fresh interactive install also asks) |
| Turn on later / replace the token | `C:\ProgramData\TwitchDiscordBot\install.cmd -EnableAutoUpdate` |
| Turn off (deletes the token) | `C:\ProgramData\TwitchDiscordBot\install.cmd -DisableAutoUpdate` |
| Check without installing | elevated PowerShell: `powershell -ExecutionPolicy Bypass -File C:\ProgramData\TwitchDiscordBot\auto-update.ps1 -Check` |
| Update now | the same without `-Check`, or Task Scheduler -> *TwitchDiscordBot Update* -> Run |
| Log | `C:\ProgramData\TwitchDiscordBot\updater\update.log` |

Silent setup with automatic updates: set `UPDATE_GITHUB_TOKEN` in the environment and add
`/MERGETASKS="autoupdate"`. The token is always passed through the environment, never on a command line.

Running the installer again (by hand or by the updater) keeps the current choice: leaving the setup wizard's box
unchecked, or omitting `--auto-update`/`-AutoUpdate`, does not turn updates off. Uninstalling (with or without
purge) removes the timer or task, the updater files and the token.

## Troubleshooting

**Typing `/` shows no TwitchBot commands, and `/setup channel` is sent as plain text.** The bot wasn't
authorized for slash commands in that server. This happens when it was invited with only the `bot` scope,
for example by an old invite link. Re-authorize it; the bot stays in the server and keeps its settings:

1. Open `https://discord.com/oauth2/authorize?client_id=<APPLICATION ID>&scope=bot+applications.commands&permissions=268585984`.
   Your *Application ID* is under *General Information* in the Developer Portal. The bot also logs this
   exact link at startup, and `python Main.py --check` prints it.
2. Pick the server and click *Authorize*.
3. Restart Discord, or press Ctrl+R, so it reloads the command list.

The bot checks this at startup. When a server is missing the permission, it posts the link in that server
once and logs a warning.

**Commands show up but say "This interaction failed".** The bot isn't running. Check the service and
`logs/bot.log`.

## After installing

In Discord, run `/setup channel` in the channel for alerts, then `/stream add <twitch name>`. `/help` lists
every command.

Upgrading from a git-checkout install made with `scripts/install-service.sh` or `install-startup.ps1`? The
installer finds it and offers to import its settings and data.
