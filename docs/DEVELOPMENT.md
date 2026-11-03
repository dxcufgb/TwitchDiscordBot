# Maintainer notes

Maintainer notes, moved out of the README. User documentation is in the wiki.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest
```

CI runs the same checks on Python 3.10–3.14. Renovate (`renovate.json`) opens PRs for dependency updates once
the [Renovate GitHub app](https://github.com/apps/renovate) is enabled for the repository.

See [AUDIT.md](../AUDIT.md) for the audit that led to the 2.0 rewrite.

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
