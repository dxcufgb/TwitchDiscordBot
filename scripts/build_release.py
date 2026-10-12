#!/usr/bin/env python3
"""Build the release files: only what is needed to install and run the bot.

    python scripts/build_release.py [--version 1.2.3] [--out dist] [--setup-exe FILE] [--code-dir DIR]

The version comes from the release tag (the Release workflow passes it); without --version it is the one in
twitchbot/__init__.py. It is stamped into the built copy only (VERSION, twitchbot/__init__.py); the repository
is never changed. Same commit + same version -> byte-identical archives.

Creates in ``--out`` (default ``dist/``), for version X.Y.Z:
    twitchdiscordbot-X.Y.Z-linux.tar.gz   one top folder twitchdiscordbot-X.Y.Z/ with install.sh at its top
    twitchdiscordbot-X.Y.Z-windows.zip
    twitchdiscordbot-X.Y.Z-setup.exe      only with --setup-exe (Inno Setup builds it on Windows)
    twitchdiscordbot-linux.tar.gz, twitchdiscordbot-windows.zip, twitchdiscordbot-setup.exe
                                          the same files under fixed names (releases/latest/download/<name>)
    legacy copies (LEGACY_ASSETS below)
    SHA256SUMS.txt                        sha256sum format, every file above
    RELEASE_NOTES.md                      the install header for the release notes (not a release file)

--code-dir DIR writes the stamped twitchbot/*.py to DIR, for the Inno Setup build (ISCC /DCodeDir=DIR).

Both archives also contain CHANGES.md: the commits since the previous v* tag (needs the git history and
tags, as in the release workflow). The bot sends it to server admins after a manual upgrade.
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
CHECKSUMS = "SHA256SUMS.txt"
NOTES = "RELEASE_NOTES.md"
# A release version: 1.2.3, or 1.2.3-rc.1 for a pre-release (same rule as .github/workflows/release.yml).
VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?$")

# ---------------------------------------------------------------------------------------------- legacy names
# Byte-identical copies of new files under the names that releases up to 2.2.0 used: {new name: legacy name}.
# The updaters of 2.2.0 and earlier only download <name>-X.Y.Z-linux.tar.gz / -windows.zip and SHA256SUMS.txt,
# whose names did not change, so no updater depends on these copies; they keep old download links and
# instructions working. Remove this block once every installation runs a version whose updater uses the new
# names (that is already the case for every installation that has automatic updates) and the owner drops them.
LEGACY_ASSETS = {
    "{name}-{version}-setup.exe": "{name}-{version}-windows-setup.exe",
}
# Today's checksum file (SHA256SUMS.txt, sha256sum format) is also the new one, so there is no legacy copy of it.
# ---------------------------------------------------------------------------------------------- end of legacy

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
    """The version in the repository (the last release's): twitchbot/__init__.py, checked against pyproject."""
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
    code = sorted(p for p in (ROOT / "twitchbot").glob("*.py") if p.name != "__init__.py")
    return [(str(p.relative_to(ROOT)), f"twitchbot/{p.name}", False) for p in code] + COMMON


def stamped_init(version: str) -> bytes:
    """twitchbot/__init__.py with ``__version__`` set to the release version (built copy only)."""
    text = read_source("twitchbot/__init__.py", crlf=False).decode("utf-8")
    text, count = re.subn(r'^__version__\s*=\s*"[^"]*"', f'__version__ = "{version}"', text, count=1, flags=re.M)
    if count != 1:
        sys.exit("Could not find __version__ in twitchbot/__init__.py")
    return text.encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_file(src: Path, dest: Path) -> None:
    dest.write_bytes(src.read_bytes())


def source_date_epoch() -> int:
    """Reproducible timestamps: SOURCE_DATE_EPOCH, else the last commit time."""
    if os.environ.get("SOURCE_DATE_EPOCH"):
        return int(os.environ["SOURCE_DATE_EPOCH"])
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=ROOT, capture_output=True, text=True, check=True)
        return int(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError, ValueError):
        return 315532800  # 1980-01-01, the earliest time a zip file can store


def git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout


def changes(version: str) -> str:
    """CHANGES.md: one line per commit since the previous release tag (or a link if git can't tell)."""
    header = f"# TwitchDiscordBot {version}\n\n"
    link = f"Release notes: https://github.com/dxcufgb/TwitchDiscordBot/releases/tag/v{version}\n"
    args = ["describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", "--exclude", f"v{version}"]
    if "-" not in version:
        args += ["--exclude", "v*-*"]  # a final release lists the changes since the previous final release
    previous = git(*args, "HEAD")
    if not previous or not previous.strip():
        return header + link
    log = git("log", "--no-merges", "--format=- %s", f"{previous.strip()}..HEAD")
    if not log or not log.strip():
        return header + link
    return header + f"Changes since {previous.strip()}:\n\n" + log.strip() + "\n\n" + link


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
    """Install header of the release notes (the workflow adds the generated notes below it)."""
    return f"""<!-- install-notes -->
## Install

**Linux** (systemd; Debian/Ubuntu, Fedora/RHEL, Arch, openSUSE, Alpine):

```bash
curl -fsSL https://raw.githubusercontent.com/dxcufgb/install/main/install.sh | sudo bash -s {NAME}
```

It asks for a fine-grained GitHub token with read-only *Contents* access to this repository (it is private).
Or download `{NAME}-linux.tar.gz`, then `tar xzf {NAME}-linux.tar.gz && cd {NAME}-{version} && sudo ./install.sh`.
`sudo ./install.sh --help` lists the options (unattended install, automatic updates, uninstall).

**Windows 10/11 or Server 2019+**: download and run `{NAME}-{version}-setup.exe`. A setup wizard
asks for the credentials, checks them, and installs everything. The setup isn't code-signed yet, so Windows
SmartScreen may warn about it; click *More info* -> *Run anyway*. For scripted installs, use
`{NAME}-{version}-windows.zip` and run `install.cmd`.

The installers set up Python if needed, install the bot and its packages, ask for the Discord/Twitch
credentials and check them, and start the bot as a service that runs at boot. Running a newer release's
installer upgrades in place and keeps your settings and data. See `README.md` in the archive for details.

Automatic updates are optional and off by default; see "Automatic updates" in `README.md`.

`{NAME}-linux.tar.gz`, `{NAME}-windows.zip` and `{NAME}-setup.exe` are the same files under names that never
change. Verify downloads with `sha256sum -c --ignore-missing SHA256SUMS.txt` (Linux) or `Get-FileHash` (Windows).
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", help="release version, e.g. 1.2.3 (default: twitchbot.__version__)")
    parser.add_argument("--out", default="dist", help="output directory (default: dist)")
    parser.add_argument("--setup-exe", type=Path, help="the Windows setup wizard built by Inno Setup, to add")
    parser.add_argument("--code-dir", type=Path, help="also write the stamped twitchbot/*.py here (Inno Setup)")
    args = parser.parse_args()

    repository_version = read_version()  # also checks that __init__.py and pyproject.toml agree
    version = (args.version or repository_version).strip().removeprefix("v")
    if not VERSION_RE.match(version):
        sys.exit(f"Not a release version: {version!r} (expected e.g. 1.2.3 or 1.2.3-rc.1)")

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    prefix = f"{NAME}-{version}"
    mtime = source_date_epoch()
    extra = {
        "VERSION": f"{version}\n".encode(),
        "CHANGES.md": changes(version).encode(),
        "twitchbot/__init__.py": stamped_init(version),
    }
    common = package_files()

    files: list[Path] = []
    linux = out / f"{prefix}-linux.tar.gz"
    windows = out / f"{prefix}-windows.zip"
    build_tar(linux, prefix, common + LINUX, extra, mtime)
    build_zip(windows, prefix, common + WINDOWS, extra, mtime)
    files += [linux, windows]
    if args.setup_exe:
        setup = out / f"{prefix}-setup.exe"
        if args.setup_exe.resolve() != setup.resolve():
            copy_file(args.setup_exe, setup)
        files.append(setup)

    # Fixed names: the same bytes, so releases/latest/download/<name> links never change.
    for path in list(files):
        fixed = out / path.name.replace(f"{prefix}-", f"{NAME}-", 1)
        copy_file(path, fixed)
        files.append(fixed)

    for new, legacy in LEGACY_ASSETS.items():
        src = out / new.format(name=NAME, version=version)
        if src in files:
            dest = out / legacy.format(name=NAME, version=version)
            copy_file(src, dest)
            files.append(dest)

    sums = "".join(f"{sha256(p)}  {p.name}\n" for p in files)
    (out / CHECKSUMS).write_text(sums, encoding="utf-8", newline="\n")
    (out / NOTES).write_text(release_notes(version), encoding="utf-8", newline="\n")

    if args.code_dir:
        code_dir = args.code_dir if args.code_dir.is_absolute() else ROOT / args.code_dir
        if code_dir.resolve() == (ROOT / "twitchbot").resolve():
            sys.exit("--code-dir must not be the repository's twitchbot folder (the version is stamped in the copy)")
        code_dir.mkdir(parents=True, exist_ok=True)
        staged = {Path(arc).name: src for src, arc, _ in common if arc.startswith("twitchbot/")}
        for old in code_dir.glob("*.py"):  # Inno Setup takes every *.py: drop modules left by an earlier build
            if old.name not in staged and old.name != "__init__.py":
                old.unlink()
        for name, src in staged.items():
            (code_dir / name).write_bytes(read_source(src, crlf=False))
        (code_dir / "__init__.py").write_bytes(extra["twitchbot/__init__.py"])

    for p in [*files, out / CHECKSUMS]:
        print(f"{p.relative_to(ROOT) if p.is_relative_to(ROOT) else p}  ({p.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
