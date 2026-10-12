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

### Install on Linux

```bash
curl -fsSL https://raw.githubusercontent.com/dxcufgb/install/main/install.sh | sudo bash -s twitchdiscordbot
```

This repository is private, so it asks for a fine-grained GitHub token with read-only *Contents* access to this
repository. Or download `twitchdiscordbot-linux.tar.gz` from the [latest release](../../releases/latest), then
`tar xzf twitchdiscordbot-linux.tar.gz`, `cd twitchdiscordbot-X.Y.Z`, `sudo ./install.sh`.

| Option | Meaning |
|---|---|
| (none) | install or upgrade in place, interactive (asks for what it needs) |
| `--yes`, `-y` | non-interactive: no questions; missing required settings → clear error |
| `--auto-update` / `--no-auto-update` | turn automatic updates on / off as part of this install |
| `--enable-auto-update` / `--disable-auto-update` | turn them on / off on an existing install, nothing else |
| `--check-update` | only check: exit 0 = up to date, 10 = newer release available, other = error |
| `--update-now` | install the latest release now (same path as the daily updater) |
| `--uninstall` [`--purge`] | remove (keep / also delete settings and data) |
| `--help` | usage: these options first, then the ones specific to this repository |

Options for this bot: `--reconfigure` (enter new credentials), `--prefix DIR`, `--user NAME`, `--no-service`,
`--skip-check`. Unattended: `sudo DISCORD_TOKEN=… TWITCH_APP_ID=… TWITCH_APP_SECRET=… ./install.sh --yes`.

### Install on Windows

Run `twitchdiscordbot-X.Y.Z-setup.exe` (or `twitchdiscordbot-setup.exe`, the same file) from the
[Releases page](../../releases), a setup wizard that also adds an entry to *Apps & features* for uninstalling.
For scripted installs, use `twitchdiscordbot-X.Y.Z-windows.zip` and run `install.cmd`.

### What the installers do

The installer:

- installs Python 3.10+ if it's missing;
- installs the bot with its own virtual environment, under `/opt/twitchdiscordbot` or `C:\ProgramData\TwitchDiscordBot`;
- asks for the Discord and Twitch credentials and verifies them;
- starts the bot as a boot-time service: a hardened systemd unit running as a `twitchbot` user, or a scheduled
  task running as *LOCAL SERVICE*.

Running a newer release's installer upgrades in place and keeps your settings and data. The `README.md` inside
the archive covers reconfiguring and uninstalling.

**Automatic updates (opt-in).** The installers can set up a daily job (systemd timer as root on Linux, scheduled
task as SYSTEM on Windows) that installs newer releases through the same upgrade path, verifies them against
`SHA256SUMS.txt`, and rolls back if the new version doesn't start. Because this repository is private, it needs
a fine-grained GitHub token with read-only *Contents* access, stored separately from `.env`. Turn it on with
`install.sh --auto-update` / `--enable-auto-update`, `install.cmd -AutoUpdate` / `-EnableAutoUpdate`, or the setup
wizard's checkbox (on Linux the one-liner's token can be reused for it); see
[packaging/README.md](packaging/README.md#automatic-updates). The logic lives in `twitchbot/updater.py` (standard
library only) with thin wrappers in `packaging/linux/auto-update.sh` and `packaging/windows/auto-update.ps1`. You still need a Discord application and a Twitch
application first (steps 1–2 below).

## Setup from source

1. **Discord application** ([Developer Portal](https://discord.com/developers/applications)): create a bot
   and copy its token. Recommended: on the same *Bot* page turn on **Server Members Intent** (under
   *Privileged Gateway Intents*). With `UPDATE_NOTIFY=admins` (the default) the bot asks for it to find server
   admins for update notifications. If it is off, Discord refuses that connection; the bot then logs one
   warning and starts again without it, sending update notices to server owners only (`python Main.py --check`
   warns about it too). Set `UPDATE_NOTIFY=owner` to silence the warning. Invite it
   with the `bot` and `applications.commands` scopes and these permissions: *View Channels, Send Messages, Embed Links, Mention @everyone/@here/All Roles,
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

## Update notifications (DMs to server admins)

When the bot starts a newer version than before (after an automatic or a manual upgrade), it sends one DM to
each server owner and administrator, listing the servers it applies to, with the release notes: from GitHub
when the auto-updater installed it, otherwise from `CHANGES.md` in the release archive (just the version and
a link if neither is available). It happens once per version; the first start after installing only records
the version. People with DMs closed are skipped. Set `UPDATE_NOTIFY` in `.env` to `admins` (default),
`owner` (server owners only) or `off`. Finding administrators needs the *Server Members Intent*; when it is
turned off in the Developer Portal, `admins` works like `owner` and the log says so once per start.

## Files

| Path | Contents |
|---|---|
| `.env` | Secrets and settings (see `.env.example`) |
| `data/state.json` | Alert channels, tracked streams, filters and roles per server, and the last bot version that ran (written atomically) |
| `data/update-notice.json` | Release notes left by the auto-updater for the update DM; deleted once read |
| `logs/bot.log` | Rotating log (5 × 2 MB) |
| `updater/github-token` | Only with automatic updates: the GitHub token (root / Administrators+SYSTEM only) |

### Upgrading from the 1.x bot

On first start without `data/state.json`, the bot imports the old `Streams.json`/`streams.json`,
`<server id>-Stream.json` files and the `STREAMS-CHANNEL-<server id>` entries in `.env`. It reuses the existing
`TW-…-Role` roles. The old files are left untouched. To redo the import, delete `data/state.json`. If an
alert channel is missing after the upgrade (the 1.x bot could corrupt `.env`), run `/setup channel` again.
The old `!` commands are gone; use the slash commands above.

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

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest
```

CI runs the same checks on Python 3.10–3.14. Renovate (`renovate.json`) opens PRs for dependency updates once
the [Renovate GitHub app](https://github.com/apps/renovate) is enabled for the repository.

See [AUDIT.md](AUDIT.md) for the audit that led to the 2.0 rewrite.

The **Security** workflow (`.github/workflows/security.yml`; GitHub's secret scanning and CodeQL are not available for
this private repository) runs on every pull request, on pushes to `main` and weekly: `Secrets (gitleaks)` scans the
whole git history, `Code (Semgrep)` runs the Semgrep registry rules, `Dependencies` runs pip-audit on
`requirements.txt` and `requirements-dev.txt`. The same checks locally (gitleaks 8.30.1, semgrep 1.180.0, pip-audit
2.10.1):

```sh
gitleaks git --redact .
semgrep scan --metrics=off --error --severity WARNING --severity ERROR \
  --config p/python --config p/javascript --config p/security-audit \
  --config p/secrets --config p/github-actions --config r/bash
pip-audit --strict -r requirements.txt
pip-audit -r requirements-dev.txt
```

Mark a false positive with `# nosemgrep: <rule-id>` and a reason (Semgrep) or a narrow, commented allowlist entry in
`.gitleaks.toml` (gitleaks).

## Releasing

**Actions → Release → Run workflow → type the version** (`1.2.3`; a leading `v` is stripped, `1.2.3-rc.1` makes a
pre-release). Fallback: publish a release `vX.Y.Z` on GitHub; the files are attached afterwards. No version bump
in the code: the version comes from the tag, and the code's `__version__` simply stays at the last release's.

The **Release** workflow runs the full CI first with that version stamped into the built files, including real
installs on Linux (systemd) and Windows (Task Scheduler and the setup wizard). It then builds the files with
`scripts/build_release.py` and publishes the release `vX.Y.Z` on the tested commit (it refuses a version whose tag
or release already exists). Nothing is published if any job fails. Release files:

| File | What |
|---|---|
| `twitchdiscordbot-X.Y.Z-linux.tar.gz` | Linux archive (top folder `twitchdiscordbot-X.Y.Z/` with `install.sh`) |
| `twitchdiscordbot-X.Y.Z-windows.zip` | Windows archive (`install.cmd`) |
| `twitchdiscordbot-X.Y.Z-setup.exe` | Windows setup wizard |
| `twitchdiscordbot-linux.tar.gz`, `twitchdiscordbot-windows.zip`, `twitchdiscordbot-setup.exe` | the same files under names that never change (`releases/latest/download/<name>`) |
| `twitchdiscordbot-X.Y.Z-windows-setup.exe` | legacy name of the setup wizard (releases up to 2.2.0) |
| `SHA256SUMS.txt` | checksums of every other file (`sha256sum -c`) |

To inspect them locally, run `python scripts/build_release.py --version 9.9.9`; they're written to `dist/`.
