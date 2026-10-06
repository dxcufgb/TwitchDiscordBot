# TwitchDiscordBot

A Discord bot that announces when Twitch streams go live. It **polls** the Twitch Helix API
(app-only client-credentials auth, no EventSub, no user tokens) and posts embeds to the channels
each server has set up with slash commands. Ships with Linux (systemd), Windows (scheduled task)
and Inno Setup installers.

## Layout
- `Main.py` – entry point (`twitchbot.bot.main()`); `--check` tests the credentials, `--version`.
- `twitchbot/bot.py` – `TwitchNotifierBot(discord.Client)`: one `Twitch` client created in
  `setup_hook` and closed in `close()`, the `poll_streams` task loop (never allowed to die),
  batching `get_users`/`get_streams` by 100 ids, embeds, logging setup.
- `twitchbot/commands.py` – slash commands (`/restart` is owner-only via `bot.is_owner`).
- `twitchbot/notify.py` – pure, I/O-free announcement logic (filters, invite stripping, chunking).
- `twitchbot/storage.py` – `StateStore` (`data/state.json`, schema v2, atomic writes, 1.x migration).
- `twitchbot/config.py` – frozen `Config` from `.env` (real environment variables win; old names
  still accepted). `twitchbot/check.py` – credential checks.
- `packaging/linux`, `packaging/windows` (install scripts, `TwitchDiscordBot.iss`), `scripts/build_release.py`.
- `AUDIT.md` – findings in the old 1.x code, all fixed in 2.0. Do not let any come back.

## Commands
- `pip install -r requirements-dev.txt`
- `ruff check .` and `ruff format --check .` (line length 120, target py310, rules E, F, W, I, UP, B)
- `pytest` (`asyncio_mode = "auto"`; fakes such as `FakeTwitch`, no network)
- CI tests Python 3.10–3.14 and really installs, upgrades and uninstalls on Linux and Windows.

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
- **Windows installer**: `.env` ACL = Administrators/SYSTEM full control + read for the run-as
  account (LOCAL SERVICE); Users/Everyone must never appear. Scripts must run on Windows
  PowerShell 5.1 and stay PSScriptAnalyzer-clean.
- **Inno Setup `.iss`**: no line may start with `#` unless it is a preprocessor directive; the
  file is read as ASCII (`tests/test_windows_setup.py`).
- **Versions**: change `twitchbot/__init__.py` and `pyproject.toml` together; the release tag must
  be `v<__version__>` (enforced by `build_release.py` and `release.yml`).
