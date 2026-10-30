# TwitchDiscordBot

A Discord bot that posts an alert, and pings a per-stream role, when the Twitch channels you track go live.

Built on **discord.py 2.7** (slash commands) and **twitchAPI 4.5** (async Helix API). Requires Python 3.10 or newer.

## Installation

You need a Discord application and a Twitch application first: see [Discord and Twitch Setup](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Discord-and-Twitch-Setup).

Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/dxcufgb/install/main/install.sh | sudo bash -s twitchdiscordbot
```

Windows: run `twitchdiscordbot-setup.exe` from the [latest release](../../releases/latest).

Installer options, scripted installs and running from source: see [Installation](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Installation) in the wiki.

## Documentation

The full guide is in the [wiki](https://github.com/dxcufgb/TwitchDiscordBot/wiki):

- [Discord and Twitch Setup](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Discord-and-Twitch-Setup): the two applications, the invite and the credentials.
- [Installation](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Installation): Linux and Windows installers, their options and what they do.
- [Commands](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Commands): every slash command and who may use it.
- [Automatic Updates](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Automatic-Updates): the opt-in daily updater.
- [Update Notifications](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Update-Notifications): the DM server admins get after an update.
- [Files and Configuration](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Files-and-Configuration): `.env`, the data files, and running from a source checkout.
- [Upgrading from 1.x](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Upgrading-from-1.x): what the first start imports.
- [Troubleshooting](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Troubleshooting): missing or old slash commands.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest
```

CI runs the same checks on Python 3.10–3.14. Renovate (`renovate.json`) opens PRs for dependency updates once
the [Renovate GitHub app](https://github.com/apps/renovate) is enabled for the repository.

See [AUDIT.md](AUDIT.md) for the audit that led to the 2.0 rewrite.

The **Security** workflow (`.github/workflows/security.yml`; GitHub's own secret scanning and CodeQL also run on this
public repository) runs on every pull request, on pushes to `main` and weekly: `Secrets (gitleaks)` scans the
whole git history, `Code (Semgrep)` runs the Semgrep registry rules, `Dependencies` runs pip-audit on
`requirements.txt` and `requirements-dev.txt`. The same checks locally (gitleaks 8.30.1, semgrep 1.180.0, pip-audit
2.10.1):

```sh
gitleaks git --redact --log-opts="--full-history --diff-filter=tuxdb --diff-merges=first-parent HEAD" .
semgrep scan --metrics=off --error --severity WARNING --severity ERROR \
  --config p/python --config p/javascript --config p/security-audit \
  --config p/secrets --config p/github-actions --config r/bash
pip-audit --strict -r requirements.txt
pip-audit --strict -r requirements-dev.txt
```

Mark a Semgrep false positive with `# nosemgrep: <rule-id>` (Python, shell, YAML, PowerShell) on the matched line or
the line immediately before it, with a reason. Mark a gitleaks false positive with a narrow, commented allowlist entry
in `.gitleaks.toml`; the file must have `[extend]` with `useDefault = true` (the workflow checks this), or it would
replace the default rules. Before scanning, the workflow checks that the pinned gitleaks, with that configuration,
detects a token generated at run time; it scans only the checked-out commit's history (`HEAD`, merge commits against
their first parent), not every branch.

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

## License

[MIT](LICENSE)
