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
Other options: `--prefix DIR`, `--user NAME`, `--no-service`, `--skip-check` (see `./install.sh --help`).

## Windows 10/11 or Windows Server

Double-click **`install.cmd`** and approve the administrator prompt. The installer:

- installs Python 3.13 with winget if needed;
- installs the bot into `C:\ProgramData\TwitchDiscordBot`;
- registers the **TwitchDiscordBot** scheduled task. The task runs at boot as *LOCAL SERVICE* and restarts the bot if it stops.

| Task | How |
|---|---|
| Logs | `C:\ProgramData\TwitchDiscordBot\logs\bot.log` |
| Restart | Task Scheduler → TwitchDiscordBot → End, then Run |
| Change credentials | `C:\ProgramData\TwitchDiscordBot\install.cmd -Reconfigure` |
| Upgrade | run `install.cmd` from the newer release folder |
| Uninstall | `C:\ProgramData\TwitchDiscordBot\uninstall.cmd` (add `-Purge` to delete settings and data) |

## After installing

In Discord, run `/setup channel` in the channel for alerts, then `/stream add <twitch name>`. `/help` lists
every command.

Upgrading from a git-checkout install made with `scripts/install-service.sh` or `install-startup.ps1`? The
installer finds it and offers to import its settings and data.
