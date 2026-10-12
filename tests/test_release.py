"""The release archives must contain exactly the runtime files, with correct modes and line endings."""

import hashlib
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

from twitchbot import __version__  # noqa: E402

CODE = {f"twitchbot/{p.name}" for p in (ROOT / "twitchbot").glob("*.py")}
COMMON = CODE | {"Main.py", "requirements.txt", ".env.example", "README.md", "VERSION", "CHANGES.md"}


def build(tmp_path, *extra, version=None):
    args = ["--out", str(tmp_path), *extra]
    if version:
        args += ["--version", version]
    subprocess.run([sys.executable, "scripts/build_release.py", *args], cwd=ROOT, check=True, capture_output=True)
    prefix = f"twitchdiscordbot-{(version or __version__).removeprefix('v')}"
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
        assert tar.extractfile(members["CHANGES.md"]).read().decode().startswith(f"# TwitchDiscordBot {__version__}\n")

    with zipfile.ZipFile(windows) as zf:
        names = {n.removeprefix(prefix + "/") for n in zf.namelist()}
        assert names == COMMON | {"install.cmd", "install.ps1", "uninstall.cmd", "start-bot.cmd", "auto-update.ps1"}
        for script in ("install.cmd", "install.ps1", "uninstall.cmd", "start-bot.cmd", "auto-update.ps1"):
            data = zf.read(f"{prefix}/{script}")
            assert data.count(b"\n") == data.count(b"\r\n"), f"{script} must use CRLF line endings"

    sums = (tmp_path / "SHA256SUMS.txt").read_text().splitlines()
    assert [line.split()[1] for line in sums] == [
        linux.name,
        windows.name,
        "twitchdiscordbot-linux.tar.gz",
        "twitchdiscordbot-windows.zip",
    ]


def test_version_is_stamped_from_the_argument(tmp_path):
    # The release workflow passes the version from the tag; the repository itself is not changed.
    before = (ROOT / "twitchbot" / "__init__.py").read_bytes()
    prefix, linux, windows = build(tmp_path, version="v9.9.9")
    assert prefix == "twitchdiscordbot-9.9.9"
    assert (ROOT / "twitchbot" / "__init__.py").read_bytes() == before
    with tarfile.open(linux) as tar:
        assert tar.extractfile(f"{prefix}/VERSION").read() == b"9.9.9\n"
        init = tar.extractfile(f"{prefix}/twitchbot/__init__.py").read().decode()
        assert '__version__ = "9.9.9"' in init
        assert tar.extractfile(f"{prefix}/CHANGES.md").read().decode().startswith("# TwitchDiscordBot 9.9.9\n")
        assert {m.name.split("/")[0] for m in tar.getmembers()} == {prefix}
    with zipfile.ZipFile(windows) as zf:
        assert '__version__ = "9.9.9"' in zf.read(f"{prefix}/twitchbot/__init__.py").decode()
        assert zf.read(f"{prefix}/VERSION") == b"9.9.9\n"


def test_release_files_fixed_and_legacy_names(tmp_path):
    setup = tmp_path / "Output.exe"
    setup.write_bytes(b"MZ fake setup")
    code = tmp_path / "code"
    out = tmp_path / "dist"
    build(out, "--setup-exe", str(setup), "--code-dir", str(code), version="9.9.9-rc.1")
    names = {
        "twitchdiscordbot-9.9.9-rc.1-linux.tar.gz",
        "twitchdiscordbot-9.9.9-rc.1-windows.zip",
        "twitchdiscordbot-9.9.9-rc.1-setup.exe",
        "twitchdiscordbot-linux.tar.gz",
        "twitchdiscordbot-windows.zip",
        "twitchdiscordbot-setup.exe",
        "twitchdiscordbot-9.9.9-rc.1-windows-setup.exe",  # legacy name (releases up to 2.2.0)
    }
    assert {p.name for p in out.iterdir()} == names | {"SHA256SUMS.txt", "RELEASE_NOTES.md"}
    sums = {}
    for line in (out / "SHA256SUMS.txt").read_text().splitlines():
        digest, name = line.split("  ")
        sums[name] = digest
    assert set(sums) == names
    for name, digest in sums.items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest, name
    for kind in ("linux.tar.gz", "windows.zip", "setup.exe"):
        assert sums[f"twitchdiscordbot-{kind}"] == sums[f"twitchdiscordbot-9.9.9-rc.1-{kind}"]
    assert sums["twitchdiscordbot-9.9.9-rc.1-windows-setup.exe"] == hashlib.sha256(b"MZ fake setup").hexdigest()
    # The stamped package for the Inno Setup build.
    assert {p.name for p in code.iterdir()} == {p.split("/")[1] for p in CODE}
    assert '__version__ = "9.9.9-rc.1"' in (code / "__init__.py").read_text()
    notes = (out / "RELEASE_NOTES.md").read_text()
    assert "twitchdiscordbot-9.9.9-rc.1-setup.exe" in notes and "sudo bash -s twitchdiscordbot" in notes


def test_checksums_match_what_the_updater_expects(tmp_path):
    # The auto-updater picks these exact asset names and verifies them against SHA256SUMS.txt.
    from twitchbot import updater

    _, linux, windows = build(tmp_path)
    sums = updater.parse_checksums((tmp_path / "SHA256SUMS.txt").read_text())
    for platform, archive in (("linux", linux), ("windows", windows)):
        assert updater.asset_name(__version__, platform) == archive.name
        assert sums[archive.name] == hashlib.sha256(archive.read_bytes()).hexdigest()


def test_reproducible(tmp_path):
    _, linux_a, windows_a = build(tmp_path / "a", version="9.9.9")
    _, linux_b, windows_b = build(tmp_path / "b", version="9.9.9")
    assert linux_a.read_bytes() == linux_b.read_bytes()
    assert windows_a.read_bytes() == windows_b.read_bytes()


def test_code_dir_is_never_the_source_package_and_drops_stale_modules(tmp_path):
    before = (ROOT / "twitchbot" / "__init__.py").read_bytes()
    main_py = (ROOT / "Main.py").read_bytes()
    for bad in ("twitchbot", ".", "..", "scripts", "build"):
        result = subprocess.run(
            [sys.executable, "scripts/build_release.py", "--out", str(tmp_path / "d"), "--version", "9.9.9",
             "--code-dir", bad],
            cwd=ROOT, capture_output=True, text=True,
        )  # fmt: skip
        assert result.returncode != 0 and "--code-dir" in result.stderr, bad
    assert (ROOT / "twitchbot" / "__init__.py").read_bytes() == before
    assert (ROOT / "Main.py").read_bytes() == main_py
    code = tmp_path / "code"
    code.mkdir()
    (code / "removed_module.py").write_text("x = 1\n")
    build(tmp_path / "d", "--code-dir", str(code), version="9.9.9")
    assert {p.name for p in code.glob("*.py")} == {p.split("/")[1] for p in CODE}


def test_repository_versions_must_agree_also_with_an_explicit_version(tmp_path, monkeypatch):
    (tmp_path / "twitchbot").mkdir()
    (tmp_path / "twitchbot" / "__init__.py").write_text('__version__ = "2.2.0"\n')
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "2.1.0"\n')
    build_release = load_build_release(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["build_release.py", "--version", "9.9.9", "--out", str(tmp_path / "dist")])
    with pytest.raises(SystemExit, match="pyproject.toml version does not match"):
        build_release.main()


@pytest.mark.parametrize("bad", ["1.2", "01.2.3", "1.2.3+build", "1.2.3-", "latest", "v"])
def test_invalid_version_is_rejected(tmp_path, bad):
    result = subprocess.run(
        [sys.executable, "scripts/build_release.py", "--out", str(tmp_path), "--version", bad],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Not a release version" in result.stderr


def test_updater_accepts_the_real_archives(tmp_path):
    from twitchbot import updater

    prefix, linux, windows = build(tmp_path)
    root = updater.extract(linux, tmp_path / "x-linux", prefix)
    assert (root / "install.sh").is_file() and (root / "auto-update.sh").is_file()
    root = updater.extract(windows, tmp_path / "x-windows", prefix)
    assert (root / "install.ps1").is_file() and (root / "auto-update.ps1").is_file()


def load_build_release(monkeypatch, root):
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_release_under_test", ROOT / "scripts" / "build_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", root)
    return module


def test_changes_skip_pre_release_tags_for_a_final_release(tmp_path, monkeypatch):
    def git(*args):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "tag.gpgSign=false",
             "-c", "commit.gpgSign=false", *args],
            cwd=tmp_path, check=True, capture_output=True,
        )  # fmt: skip

    git("init", "-q")
    for message, tag in (("first", "v2.0.0"), ("second", "v2.1.0-rc.1"), ("third", None)):
        git("commit", "-q", "--allow-empty", "-m", message)
        if tag:
            git("tag", tag)
    build_release = load_build_release(monkeypatch, tmp_path)

    final = build_release.changes("2.1.0")
    assert "Changes since v2.0.0:" in final and "- second" in final and "- third" in final
    pre = build_release.changes("2.1.0-rc.2")
    assert "Changes since v2.1.0-rc.1:" in pre and "- third" in pre and "- second" not in pre
