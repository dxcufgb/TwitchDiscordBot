# TwitchDiscordBot

A Discord bot that posts a message (and pings a per-stream role) when the Twitch channels you follow go live.
Type `!help` in Discord for the command list.

## Setup

1. Install Python 3.10–3.12.
2. Create a `.env` file next to `Main.py`:

   ```
   TOKEN=<discord bot token>
   TWITCH-APP-ID=<twitch client id>
   TWITCH-APP-SECRET=<twitch client secret>
   ```

3. Start the bot. The start scripts create a `.venv` and install `requirements.txt` on first run.

   | OS | Run once | Keep restarting on crash |
   |---|---|---|
   | Linux / macOS | `./start-bot.sh` | `./start-bot.sh --watch` |
   | Windows | `start-bot.cmd` | `start-bot.cmd --watch` |

   You can call the scripts from any directory: they always switch to the bot folder first.

## Start automatically at boot

**Linux (systemd)**

```bash
sudo ./scripts/install-service.sh            # install, enable and start
journalctl -u twitchdiscordbot -f            # follow logs
sudo ./scripts/install-service.sh --uninstall
```

**Windows (Task Scheduler)**, from an elevated PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-startup.ps1            # at boot
powershell -ExecutionPolicy Bypass -File .\scripts\install-startup.ps1 -AtLogon   # at logon (no admin needed)
powershell -ExecutionPolicy Bypass -File .\scripts\install-startup.ps1 -Uninstall
```

Console output goes to `bot-console.log`.

## Audit

See [AUDIT.md](AUDIT.md) for known issues and suggested improvements.
