"""The release archives must contain exactly the runtime files, with correct modes and line endings."""

import hashlib
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from twitchbot import __version__  # noqa: E402

CODE = {f"twitchbot/{p.name}" for p in (ROOT / "twitchbot").glob("*.py")}
COMMON = CODE | {"Main.py", "requirements.txt", ".env.example", "README.md", "VERSION"}


def build(tmp_path):
    subprocess.run(
        [sys.executable, "scripts/build_release.py", "--out", str(tmp_path), "--expect-version", f"v{__version__}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    prefix = f"twitchdiscordbot-{__version__}"
    return prefix, tmp_path / f"{prefix}-linux.tar.gz", tmp_path / f"{prefix}-windows.zip"


def test_archive_contents(tmp_path):
    prefix, linux, windows = build(tmp_path)

    with tarfile.open(linux) as tar:
        members = {m.name.removeprefix(prefix + "/"): m for m in tar.getmembers()}
        assert set(members) == COMMON | {"install.sh", "start-bot.sh", "auto-update.sh"}
        assert members["install.sh"].mode == 0o755
        assert members["auto-update.sh"].mode == 0o755
        assert members["Main.py"].mode == 0o644
        assert b"\r\n" not in tar.extractfile(members["install.sh"]).read()
        assert tar.extractfile(members["VERSION"]).read().decode().strip() == __version__

    with zipfile.ZipFile(windows) as zf:
        names = {n.removeprefix(prefix + "/") for n in zf.namelist()}
        assert names == COMMON | {"install.cmd", "install.ps1", "uninstall.cmd", "start-bot.cmd", "auto-update.ps1"}
        for script in ("install.cmd", "install.ps1", "uninstall.cmd", "start-bot.cmd", "auto-update.ps1"):
            data = zf.read(f"{prefix}/{script}")
            assert data.count(b"\n") == data.count(b"\r\n"), f"{script} must use CRLF line endings"

    sums = (tmp_path / "SHA256SUMS.txt").read_text().splitlines()
    assert [line.split()[1] for line in sums] == [linux.name, windows.name]


def test_checksums_match_what_the_updater_expects(tmp_path):
    # The auto-updater picks these exact asset names and verifies them against SHA256SUMS.txt.
    from twitchbot import updater

    _, linux, windows = build(tmp_path)
    sums = updater.parse_checksums((tmp_path / "SHA256SUMS.txt").read_text())
    for platform, archive in (("linux", linux), ("windows", windows)):
        assert updater.asset_name(__version__, platform) == archive.name
        assert sums[archive.name] == hashlib.sha256(archive.read_bytes()).hexdigest()


def test_reproducible(tmp_path):
    _, linux_a, windows_a = build(tmp_path / "a")
    _, linux_b, windows_b = build(tmp_path / "b")
    assert linux_a.read_bytes() == linux_b.read_bytes()
    assert windows_a.read_bytes() == windows_b.read_bytes()


def test_wrong_tag_is_rejected(tmp_path):
    result = subprocess.run(
        [sys.executable, "scripts/build_release.py", "--out", str(tmp_path), "--expect-version", "v0.0.0"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "does not match" in result.stderr


def test_updater_accepts_the_real_archives(tmp_path):
    from twitchbot import updater

    prefix, linux, windows = build(tmp_path)
    root = updater.extract(linux, tmp_path / "x-linux", prefix)
    assert (root / "install.sh").is_file() and (root / "auto-update.sh").is_file()
    root = updater.extract(windows, tmp_path / "x-windows", prefix)
    assert (root / "install.ps1").is_file() and (root / "auto-update.ps1").is_file()
