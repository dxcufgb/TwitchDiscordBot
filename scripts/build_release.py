#!/usr/bin/env python3
"""Build the release archives: only what is needed to install and run the bot.

    python scripts/build_release.py [--expect-version 2.0.0] [--out dist]

Creates in ``dist/``:
    twitchdiscordbot-<version>-linux.tar.gz
    twitchdiscordbot-<version>-windows.zip
    SHA256SUMS.txt
    RELEASE_NOTES.md
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import re
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "twitchdiscordbot"

# (source path relative to the repo, path inside the archive, executable?)
COMMON = [
    ("Main.py", "Main.py", False),
    ("requirements.txt", "requirements.txt", False),
    (".env.example", ".env.example", False),
    ("packaging/README.md", "README.md", False),
]
LINUX = [
    ("packaging/linux/install.sh", "install.sh", True),
    ("start-bot.sh", "start-bot.sh", True),
    ("packaging/linux/auto-update.sh", "auto-update.sh", True),
]
WINDOWS = [
    ("packaging/windows/install.cmd", "install.cmd", False),
    ("packaging/windows/install.ps1", "install.ps1", False),
    ("packaging/windows/uninstall.cmd", "uninstall.cmd", False),
    ("start-bot.cmd", "start-bot.cmd", False),
    ("packaging/windows/auto-update.ps1", "auto-update.ps1", False),
]


def read_version() -> str:
    text = (ROOT / "twitchbot" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        sys.exit("Could not find __version__ in twitchbot/__init__.py")
    version = match.group(1)
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project_version = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    if not project_version or project_version.group(1) != version:
        sys.exit(f"pyproject.toml version does not match twitchbot.__version__ ({version})")
    return version


def package_files() -> list[tuple[str, str, bool]]:
    code = sorted(p for p in (ROOT / "twitchbot").glob("*.py"))
    return [(str(p.relative_to(ROOT)), f"twitchbot/{p.name}", False) for p in code] + COMMON


def source_date_epoch() -> int:
    """Reproducible timestamps: SOURCE_DATE_EPOCH, else the last commit time."""
    if os.environ.get("SOURCE_DATE_EPOCH"):
        return int(os.environ["SOURCE_DATE_EPOCH"])
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=ROOT, capture_output=True, text=True, check=True)
        return int(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError, ValueError):
        return 315532800  # 1980-01-01, the earliest time a zip file can store


def read_source(rel: str, *, crlf: bool) -> bytes:
    data = (ROOT / rel).read_bytes().replace(b"\r\n", b"\n")
    return data.replace(b"\n", b"\r\n") if crlf else data


def build_tar(path: Path, prefix: str, files: list[tuple[str, str, bool]], extra: dict[str, bytes], mtime: int) -> None:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        entries = [(arc, read_source(src, crlf=False), exe) for src, arc, exe in files]
        entries += [(arc, data, False) for arc, data in extra.items()]
        for arc, data, exe in sorted(entries):
            info = tarfile.TarInfo(f"{prefix}/{arc}")
            info.size = len(data)
            info.mode = 0o755 if exe else 0o644
            info.mtime = mtime
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(data))
    with path.open("wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=mtime, filename="") as gz:
        gz.write(raw.getvalue())


def build_zip(path: Path, prefix: str, files: list[tuple[str, str, bool]], extra: dict[str, bytes], mtime: int) -> None:
    date_time = time.gmtime(max(mtime, 315532800))[:6]
    entries = [(arc, read_source(src, crlf=src.endswith((".cmd", ".ps1")))) for src, arc, _ in files]
    entries += list(extra.items())
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for arc, data in sorted(entries):
            info = zipfile.ZipInfo(f"{prefix}/{arc}", date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)


def release_notes(version: str) -> str:
    return f"""## Install

**Linux** (systemd; Debian/Ubuntu, Fedora/RHEL, Arch, openSUSE, Alpine):

```bash
tar xzf {NAME}-{version}-linux.tar.gz
cd {NAME}-{version}
sudo ./install.sh
```

**Windows 10/11 or Server 2019+**: download and run `{NAME}-{version}-windows-setup.exe`. A setup wizard
asks for the credentials, checks them, and installs everything. The setup isn't code-signed yet, so Windows
SmartScreen may warn about it; click *More info* -> *Run anyway*. For scripted installs, use
`{NAME}-{version}-windows.zip` and run `install.cmd`.

The installers set up Python if needed, install the bot and its packages, ask for the Discord/Twitch
credentials and check them, and start the bot as a service that runs at boot. Running a newer release's
installer upgrades in place and keeps your settings and data. See `README.md` in the archive for details.

Automatic updates are optional and off by default; see "Automatic updates" in `README.md`.

Verify downloads with `sha256sum -c SHA256SUMS.txt` (Linux) or `Get-FileHash` (Windows).
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--expect-version", help="fail unless the package version equals this (e.g. from the git tag)")
    parser.add_argument("--out", default="dist", help="output directory (default: dist)")
    args = parser.parse_args()

    version = read_version()
    if args.expect_version and args.expect_version.lstrip("v") != version:
        sys.exit(f"Tag version {args.expect_version} does not match twitchbot.__version__ {version}")

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    prefix = f"{NAME}-{version}"
    mtime = source_date_epoch()
    extra = {"VERSION": f"{version}\n".encode()}
    common = package_files()

    linux = out / f"{prefix}-linux.tar.gz"
    windows = out / f"{prefix}-windows.zip"
    build_tar(linux, prefix, common + LINUX, extra, mtime)
    build_zip(windows, prefix, common + WINDOWS, extra, mtime)

    sums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in (linux, windows))
    (out / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")
    (out / "RELEASE_NOTES.md").write_text(release_notes(version), encoding="utf-8")
    for p in (linux, windows, out / "SHA256SUMS.txt"):
        print(f"{p.relative_to(ROOT) if p.is_relative_to(ROOT) else p}  ({p.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
