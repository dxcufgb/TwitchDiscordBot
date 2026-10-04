# TwitchDiscordBot

A Discord bot that posts an alert, and pings a per-stream role, when the Twitch channels you track go live.

Built on **discord.py 2.7** (slash commands) and **twitchAPI 4.5** (async Helix API). Requires Python 3.10 or newer.

## Commands

| Who | Command | What it does |
|---|---|---|
| Everyone | `/notify list` | Streams tracked in this server (🔔 = you get pinged) |
| Everyone | `/notify subscribe <channel>` / `/notify unsubscribe <channel>` | Turn pings for a stream on or off |
| Everyone | `/help` | Command overview |
| Managers | `/setup channel [channel]` · `/setup show` · `/setup clear` | Where live alerts are posted |
| Managers | `/stream add <channel>` · `/stream remove <channel> [delete_role]` · `/stream list` | Track Twitch channels. `add` creates a `TW-<name>-Role` role |
| Managers | `/filter add <channel> <text>` · `/filter clear <channel>` · `/filter list` | Only alert when the title contains the text (case-insensitive) |
| Bot owner | `/restart` | Exit so the service manager (or `--watch`) restarts the bot |

"Managers" means members with **Manage Server** by default. A server admin can change who may use each
command under *Server Settings → Integrations → TwitchBot*.

## Install from a release (recommended)

Download the archive for your OS from the [Releases page](../../releases). Each archive holds only the bot and
an installer:

- **Linux:** `tar xzf twitchdiscordbot-<version>-linux.tar.gz && cd twitchdiscordbot-<version> && sudo ./install.sh`
- **Windows:** extract `twitchdiscordbot-<version>-windows.zip` and double-click `install.cmd`

The installer:

- installs Python 3.10+ if it's missing;
- installs the bot with its own virtual environment, under `/opt/twitchdiscordbot` or `C:\ProgramData\TwitchDiscordBot`;
- asks for the Discord and Twitch credentials and verifies them;
- starts the bot as a boot-time service: a hardened systemd unit running as a `twitchbot` user, or a scheduled
  task running as *LOCAL SERVICE*.

Running a newer release's installer upgrades in place and keeps your settings and data. The `README.md` inside
the archive covers reconfiguring and uninstalling. You still need a Discord application and a Twitch
application first (steps 1–2 below).

## Setup from source

1. **Discord application** ([Developer Portal](https://discord.com/developers/applications)): create a bot
   and copy its token. No privileged intents are needed. Invite it with the `bot` and `applications.commands`
   scopes and these permissions: *View Channels, Send Messages, Embed Links, Mention @everyone/@here/All Roles,
   Manage Roles*.
2. **Twitch application** ([Twitch dev console](https://dev.twitch.tv/console/apps)): register an app and copy
   its Client ID and secret.
3. Copy `.env.example` to `.env` and fill in `DISCORD_TOKEN`, `TWITCH_APP_ID` and `TWITCH_APP_SECRET`.
   `python Main.py --check` verifies them without starting the bot.
4. Start the bot. The start scripts create a `.venv` and install `requirements.txt` on first run:

   | OS | Run once | Keep restarting on crash |
   |---|---|---|
   | Linux / macOS | `./start-bot.sh` | `./start-bot.sh --watch` |
   | Windows | `start-bot.cmd` | `start-bot.cmd --watch` |

   You can call the scripts from any directory.
5. In Discord, run `/setup channel` in the alert channel, then `/stream add <twitch name>`.

Slash commands can take up to an hour to appear after the first global sync. For instant updates while testing,
set `DEV_GUILD_ID` in `.env`.

## Start automatically at boot (source checkout)

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

## Files

| Path | Contents |
|---|---|
| `.env` | Secrets and settings (see `.env.example`) |
| `data/state.json` | Alert channels, tracked streams, filters and roles per server (written atomically) |
| `logs/bot.log` | Rotating log (5 × 2 MB) |

### Upgrading from the 1.x bot

On first start without `data/state.json`, the bot imports the old `Streams.json`/`streams.json`,
`<server id>-Stream.json` files and the `STREAMS-CHANNEL-<server id>` entries in `.env`. It reuses the existing
`TW-…-Role` roles. The old files are left untouched. To redo the import, delete `data/state.json`. If an
alert channel is missing after the upgrade (the 1.x bot could corrupt `.env`), run `/setup channel` again.
The old `!` commands are gone; use the slash commands above.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest
```

CI runs the same checks on Python 3.10–3.13. Renovate (`renovate.json`) opens PRs for dependency updates once
the [Renovate GitHub app](https://github.com/apps/renovate) is enabled for the repository.

See [AUDIT.md](AUDIT.md) for the audit that led to the 2.0 rewrite.

## Releasing

1. Bump the version in `twitchbot/__init__.py` and `pyproject.toml` (they must match) and merge to `main`.
2. Tag and push: `git tag v2.0.0 && git push origin v2.0.0`.

The **Release** workflow runs the full CI, including real installs on Linux (systemd) and Windows (Task
Scheduler). It then builds the archives with `scripts/build_release.py` and publishes a GitHub release with the
archives, `SHA256SUMS.txt` and generated notes. Tags with a hyphen (`v2.1.0-rc1`) become pre-releases. To
inspect the archives locally, run `python scripts/build_release.py`; they're written to `dist/`.

