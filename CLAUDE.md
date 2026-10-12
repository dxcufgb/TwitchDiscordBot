# TwitchDiscordBot

A Discord bot that announces when Twitch streams go live. It **polls** the Twitch Helix API
(app-only client-credentials auth, no EventSub, no user tokens) and posts embeds to the channels
each server has set up with slash commands. Ships with Linux (systemd), Windows (scheduled task)
and Inno Setup installers, an opt-in auto-updater, and a DM with the release notes to server admins after
an update.

## Layout
- `Main.py` – entry point (`twitchbot.bot.main()`); `--check` tests the credentials, `--version`.
- `twitchbot/bot.py` – `TwitchNotifierBot(discord.Client)`: one `Twitch` client created in
  `setup_hook` and closed in `close()`, the `poll_streams` task loop (never allowed to die),
  batching `get_users`/`get_streams` by 100 ids, embeds, logging setup. `run_bot` reconnects in
  `owner` mode (without the Server Members intent) when Discord refuses that intent.
- `twitchbot/commands.py` – slash commands (`/restart` is owner-only via `bot.is_owner`).
- `twitchbot/notify.py` – pure, I/O-free announcement logic (filters, invite stripping, chunking).
- `twitchbot/update_notify.py` – after an update, DMs the release notes once per person (owner, plus
  administrators with the Server Members intent); `UPDATE_NOTIFY` = `admins` (default) / `owner` / `off`.
  Version recorded in `state.json` before sending; notes from `data/update-notice.json` or `CHANGES.md`.
- `twitchbot/updater.py` – stdlib-only release check/download/`SHA256SUMS.txt` verification, run as a
  script (`python -I`) by `packaging/linux/auto-update.sh` (root, daily timer) and
  `packaging/windows/auto-update.ps1` (SYSTEM, daily task); it must not import the rest of the bot.
- `twitchbot/storage.py` – `StateStore` (`data/state.json`, schema v2, atomic writes, 1.x migration).
- `twitchbot/config.py` – frozen `Config` from `.env` (real environment variables win; old names
  still accepted). `twitchbot/check.py` – credential checks.
- `packaging/linux`, `packaging/windows` (install scripts, `TwitchDiscordBot.iss`), `scripts/build_release.py`
  (`--version X.Y.Z` stamps the version into the built copy; release file names, `LEGACY_ASSETS`).
- `AUDIT.md` – findings in the old 1.x code, all fixed in 2.0. Do not let any come back.

## Commands
- `pip install -r requirements-dev.txt`
- `ruff check .` and `ruff format --check .` (line length 120, target py310, rules E, F, W, I, UP, B)
- `pytest` (`asyncio_mode = "auto"`; fakes such as `FakeTwitch`, no network)
- CI tests Python 3.10–3.14 and really installs, upgrades and uninstalls on Linux and Windows.
- `python scripts/build_release.py --version 9.9.9` builds the release files into `dist/`.
- Security (`.github/workflows/security.yml`, required checks `Secrets (gitleaks)`, `Code (Semgrep)`, `Dependencies`; push to main, PRs, weekly): locally `gitleaks git --redact .`, `semgrep scan --metrics=off --error --severity WARNING --severity ERROR --config p/python --config p/javascript --config p/security-audit --config p/secrets --config p/github-actions --config p/bash`, and `pip-audit --strict -r requirements.txt` and `pip-audit -r requirements-dev.txt`. False positives: `# nosemgrep: <rule-id>` + reason, or `.gitleaks.toml`.

## Install on Linux
`curl -fsSL https://raw.githubusercontent.com/dxcufgb/install/main/install.sh | sudo bash -s twitchdiscordbot`
(private repo: it asks for a fine-grained token with read-only *Contents* access to this repository), or download
`twitchdiscordbot-linux.tar.gz`, `tar xzf`, `cd twitchdiscordbot-X.Y.Z`, `sudo ./install.sh`. Common options (listed
first by `--help`): (none), `--yes`/`-y`, `--auto-update`/`--no-auto-update`,
`--enable-auto-update`/`--disable-auto-update`, `--check-update` (exit 0 up to date / 10 newer / other error),
`--update-now`, `--uninstall [--purge]`, `--help`. Then this bot's: `--reconfigure`, `--prefix`, `--user`,
`--no-service`, `--skip-check`. Existing options must keep working.

## Releasing
Actions → Release → Run workflow → type the version (`1.2.3`, a leading `v` is stripped; `1.2.3-rc.1` is a
pre-release). Fallback: publish a release `vX.Y.Z` on GitHub, files are attached afterwards. No version bump in the
code: the version comes from the tag and is stamped into the built files only (`VERSION`, `twitchbot/__init__.py`;
the setup wizard gets the stamped package via `ISCC /DCodeDir=`). Release files: `twitchdiscordbot-X.Y.Z-linux.tar.gz`,
`-windows.zip`, `-setup.exe`, the same under fixed names (`twitchdiscordbot-linux.tar.gz`, …), the legacy
`twitchdiscordbot-X.Y.Z-windows-setup.exe`, and `SHA256SUMS.txt` covering all of them.

## Conventions
- `from __future__ import annotations`, modern type hints, frozen dataclasses, `TYPE_CHECKING`
  imports to avoid cycles.
- `log = logging.getLogger(__name__)`, %-style arguments, `log.exception` in except blocks.
- Interaction replies go through `_reply()` (ephemeral by default).
- Runtime dependencies are pinned in `requirements.txt` and updated by Renovate.

## Review focus
- **No blocking calls in async code**: no `requests`, no `time.sleep`, no new `Twitch()` per call
  (AUDIT.md H6). Use twitchAPI 4.x `await` / `async for`. `store.save()` is small sync I/O and
  accepted as is.
- **Twitch rate limits**: keep the single client, the 100-id batches and `POLL_INTERVAL >= 15`.
- **Secrets**: never log the Discord token, Twitch app secret or `.env` contents; error messages
  name the setting and file, never the value. CI greps the log for the exact text
  "Discord rejected the bot token" – do not change it casually.
- **Linux installer**: `.env` stays `root:twitchbot 640`; the service runs as `User=twitchbot`;
  upgrades keep `.env`; `--uninstall --purge` removes everything. Keep it shellcheck-clean.
  Program files and `.venv` are `root:root` and not group/other-writable (`secure_program_files`); the
  bot writes only to `data/` and `logs/`.
- **Auto-update** stays opt-in and runs as root / SYSTEM. The GitHub token lives in `updater/github-token`
  (root 600 / Administrators+SYSTEM only), goes only to `api.github.com`, never on a command line or in a
  log. Root must never run code the bot's account can change: a `.venv` that is not root-only is rebuilt
  (or refused), a Python under `/home` or `/root` is refused, and a symlinked `.env`/`data`/`logs` is
  refused rather than followed. A failed upgrade is rolled back and the
  rollback verified; if it fails, "ROLLBACK FAILED" is logged and the backup kept (Linux:
  `/var/lib/twitchdiscordbot-update/`).
- **Windows installer**: `.env` ACL = Administrators/SYSTEM full control + read for the run-as
  account (LOCAL SERVICE); Users/Everyone must never appear. Scripts must run on Windows
  PowerShell 5.1 and stay PSScriptAnalyzer-clean.
- **Inno Setup `.iss`**: no line may start with `#` unless it is a preprocessor directive; the
  file is read as ASCII (`tests/test_windows_setup.py`).
- **Versions**: the release tag is the version; never bump it in the code for a release. If you do change
  `twitchbot/__init__.py`, change `pyproject.toml` with it (they must agree). Code that shows or compares the
  running version must use `__version__` / `VERSION` of the installed copy (stamped at build time).
- **Updater compatibility**: installed updaters (2.2.0 and later) download `twitchdiscordbot-X.Y.Z-linux.tar.gz` /
  `-windows.zip`, verify them against `SHA256SUMS.txt` and run the archive's `install.sh --yes --skip-check --prefix
  … --user … [--no-service]` / `install.ps1 -InstallDir … -Yes -SkipCheck [-NoService]`. Keep those names, that
  archive layout and those options working.
