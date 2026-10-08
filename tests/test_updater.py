"""Auto-update core logic (twitchbot/updater.py), with a fake GitHub: no network."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from twitchbot import updater
from twitchbot.updater import GitHub, Response, UpdateError

TOKEN = "github_pat_SECRET123"
API = "https://api.github.com/repos/dxcufgb/TwitchDiscordBot"
CDN = "https://release-assets.githubusercontent.com"


def make_tar(version: str, extra: dict[str, bytes] | None = None) -> bytes:
    prefix = f"twitchdiscordbot-{version}"
    files = {"Main.py": b"print()\n", "twitchbot/__init__.py": b"", "install.sh": b"#!/bin/sh\n"}
    files.update(extra or {})
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name if name.startswith("/") or name.startswith("..") else f"{prefix}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return raw.getvalue()


def make_zip(version: str) -> bytes:
    prefix = f"twitchdiscordbot-{version}"
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as zf:
        for name in ("Main.py", "twitchbot/__init__.py", "install.ps1"):
            zf.writestr(f"{prefix}/{name}", b"x")
    return raw.getvalue()


class FakeGitHub:
    """Serves a release like GitHub does: API asset URLs answer 302 to a pre-signed CDN URL."""

    def __init__(self, version="2.2.0", *, draft=False, prerelease=False, archive=None, sums=None, assets=None):
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.version = version
        tar = make_tar(version) if archive is None else archive
        name = f"twitchdiscordbot-{version}-linux.tar.gz"
        zname = f"twitchdiscordbot-{version}-windows.zip"
        zdata = make_zip(version)
        if sums is None:
            sums = (
                f"{hashlib.sha256(tar).hexdigest()}  {name}\n{hashlib.sha256(zdata).hexdigest()}  {zname}\n"
            ).encode()
        self.files = {f"{CDN}/1": tar, f"{CDN}/2": sums, f"{CDN}/3": zdata}
        names = assets if assets is not None else [name, "SHA256SUMS.txt", zname]
        ids = {name: 1, "SHA256SUMS.txt": 2, zname: 3}
        self.release = {
            "tag_name": f"v{version}",
            "draft": draft,
            "prerelease": prerelease,
            "assets": [{"name": n, "url": f"{API}/releases/assets/{ids[n]}"} for n in names],
        }

    def __call__(self, url, headers, timeout):
        assert timeout > 0
        self.calls.append((url, dict(headers)))
        if url == f"{API}/releases/latest":
            return Response(200, {}, iter([json.dumps(self.release).encode()]))
        if url.startswith(f"{API}/releases/assets/"):
            assert headers["Accept"] == "application/octet-stream"
            return Response(302, {"location": f"{CDN}/{url.rsplit('/', 1)[1]}"}, iter([b""]))
        if url in self.files:
            return Response(200, {}, iter([self.files[url]]))
        return Response(404, {}, iter([b""]))


@pytest.fixture
def install(tmp_path):
    folder = tmp_path / "install"
    folder.mkdir()
    (folder / "VERSION").write_text("2.1.1\n")
    token = tmp_path / "github-token"
    token.write_text(TOKEN + "\n")
    return folder, token


def run(install, fake, *args):
    folder, token = install
    return updater.main(["--install-dir", str(folder), "--token-file", str(token), "--platform", "linux", *args], fake)


# ------------------------------------------------------------------------------------------- versions


@pytest.mark.parametrize(
    ("candidate", "installed", "newer"),
    [
        ("v2.1.2", "2.1.1", True),
        ("v2.10.0", "2.9.9", True),  # numeric, not string comparison
        ("v3", "2.9.9", True),
        ("v2.1.1", "2.1.1", False),
        ("v2.1", "2.1.0", False),
        ("v2.1.0", "2.1.1", False),  # never downgrade
        ("v2.2.0-rc1", "2.1.1", False),  # pre-release
        ("latest", "2.1.1", False),
        ("v2.2.0", "garbage", False),
    ],
)
def test_is_newer(candidate, installed, newer):
    assert updater.is_newer(candidate, installed) is newer


def test_installed_version_falls_back_to_package(tmp_path):
    (tmp_path / "twitchbot").mkdir()
    (tmp_path / "twitchbot" / "__init__.py").write_text('__version__ = "2.0.1"\n')
    assert updater.installed_version(tmp_path) == "2.0.1"
    (tmp_path / "VERSION").write_text("2.1.0\n")
    assert updater.installed_version(tmp_path) == "2.1.0"


def test_installed_version_missing(tmp_path):
    with pytest.raises(UpdateError):
        updater.installed_version(tmp_path)


def test_parse_checksums():
    sums = updater.parse_checksums("A" * 64 + "  a.tar.gz\r\n" + "b" * 64 + " *b.zip\njunk\n")
    assert sums == {"a.tar.gz": "a" * 64, "b.zip": "b" * 64}


# ------------------------------------------------------------------------------------------- check


def test_up_to_date(install):
    fake = FakeGitHub(version="2.1.1")
    assert run(install, fake, "check") == updater.EXIT_UP_TO_DATE
    assert [url for url, _ in fake.calls] == [f"{API}/releases/latest"]


def test_update_available(install, capsys):
    assert run(install, FakeGitHub(), "check") == updater.EXIT_OK
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("flags", [{"draft": True}, {"prerelease": True}])
def test_drafts_and_prereleases_are_ignored(install, flags):
    assert run(install, FakeGitHub(**flags), "check") == updater.EXIT_UP_TO_DATE


def test_older_release_is_not_installed(install):
    assert run(install, FakeGitHub(version="2.0.0"), "check") == updater.EXIT_UP_TO_DATE


@pytest.mark.parametrize(("status", "text"), [(401, "rejected the token"), (404, "no published release")])
def test_http_errors_are_explained(install, capsys, status, text):
    def fake(url, headers, timeout):
        return Response(status, {}, iter([b"{}"]))

    assert run(install, fake, "check") == updater.EXIT_ERROR
    err = capsys.readouterr().err
    assert text in err
    assert TOKEN not in err


def test_missing_token_file(tmp_path, capsys):
    code = updater.main(["--install-dir", str(tmp_path), "--token-file", str(tmp_path / "nope"), "check"], FakeGitHub())
    assert code == updater.EXIT_ERROR
    assert "Cannot read the GitHub token file" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------- download


def test_download_verifies_and_extracts(install, tmp_path, capsys):
    fake = FakeGitHub()
    dest = tmp_path / "work"
    assert run(install, fake, "download", "--dest", str(dest)) == updater.EXIT_OK
    root = Path(capsys.readouterr().out.strip().splitlines()[-1])
    assert root == dest / "release" / "twitchdiscordbot-2.2.0"
    assert (root / "install.sh").is_file() and (root / "Main.py").is_file()
    assert not (dest / "download").exists()


def test_windows_asset(install, tmp_path, capsys):
    folder, token = install
    fake = FakeGitHub()
    args = ["--install-dir", str(folder), "--token-file", str(token), "--platform", "windows"]
    assert updater.main([*args, "download", "--dest", str(tmp_path / "w")], fake) == updater.EXIT_OK
    root = Path(capsys.readouterr().out.strip())
    assert (root / "install.ps1").is_file()
    assert any(url.endswith("/assets/3") for url, _ in fake.calls)


def test_token_only_sent_to_api_github_com(install, tmp_path, capsys):
    fake = FakeGitHub()
    log_file = tmp_path / "update.log"
    assert run(install, fake, "--log-file", str(log_file), "download", "--dest", str(tmp_path / "work")) == 0
    for url, headers in fake.calls:
        if url.startswith("https://api.github.com/"):
            assert headers["Authorization"] == f"Bearer {TOKEN}"
        else:
            assert "Authorization" not in headers, url
            assert TOKEN not in json.dumps(headers)
    assert any(url.startswith(CDN) for url, _ in fake.calls)
    out = capsys.readouterr()
    assert "SHA-256 verified" in log_file.read_text()
    assert TOKEN not in out.out + out.err + log_file.read_text()


def test_checksum_mismatch_is_rejected(install, tmp_path):
    name = "twitchdiscordbot-2.2.0-linux.tar.gz"
    fake = FakeGitHub(sums=f"{'0' * 64}  {name}\n".encode())
    dest = tmp_path / "work"
    assert run(install, fake, "download", "--dest", str(dest)) == updater.EXIT_ERROR
    assert not (dest / "release").exists()
    assert not (dest / "download" / name).exists()


def test_checksums_file_is_required(install, tmp_path):
    fake = FakeGitHub(assets=["twitchdiscordbot-2.2.0-linux.tar.gz"])
    assert run(install, fake, "download", "--dest", str(tmp_path / "w")) == updater.EXIT_ERROR
    assert not any("/assets/" in url for url, _ in fake.calls)


def test_missing_platform_asset(install, tmp_path):
    fake = FakeGitHub(assets=["SHA256SUMS.txt", "twitchdiscordbot-2.2.0-windows.zip"])
    assert run(install, fake, "download", "--dest", str(tmp_path / "w")) == updater.EXIT_ERROR


@pytest.mark.parametrize("bad", ["../evil.sh", "/etc/evil"])
def test_unsafe_archive_paths_are_rejected(install, tmp_path, bad):
    fake = FakeGitHub(archive=make_tar("2.2.0", {bad: b"x"}))
    assert run(install, fake, "download", "--dest", str(tmp_path / "w")) == updater.EXIT_ERROR
    assert not (tmp_path / "evil.sh").exists()


def test_symlinks_in_archive_are_rejected(tmp_path):
    raw = tmp_path / "a.tar.gz"
    with tarfile.open(raw, "w:gz") as tar:
        info = tarfile.TarInfo("twitchdiscordbot-1.0.0/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    with pytest.raises(UpdateError, match="Unsupported entry"):
        updater.extract(raw, tmp_path / "out", "twitchdiscordbot-1.0.0")


def test_dest_must_be_empty(install, tmp_path):
    dest = tmp_path / "w"
    dest.mkdir()
    (dest / "x").write_text("x")
    assert run(install, FakeGitHub(), "download", "--dest", str(dest)) == updater.EXIT_ERROR


# ------------------------------------------------------------------------------------------- HTTP rules


def test_plain_http_redirect_is_refused():
    def fake(url, headers, timeout):
        return Response(302, {"location": "http://example.com/x"}, iter([b""]))

    with pytest.raises(UpdateError, match="non-HTTPS"):
        GitHub(TOKEN, fake).get_json(f"{API}/releases/latest")


def test_redirect_loop_is_bounded():
    def fake(url, headers, timeout):
        return Response(302, {"location": url}, iter([b""]))

    with pytest.raises(UpdateError, match="Too many redirects"):
        GitHub(TOKEN, fake).get_json(f"{API}/releases/latest")


def test_download_size_is_capped(tmp_path):
    def fake(url, headers, timeout):
        return Response(200, {}, iter([b"x" * 100, b"x" * 100]))

    with pytest.raises(UpdateError, match="limit"):
        GitHub(TOKEN, fake).download(f"{API}/releases/assets/1", tmp_path / "f", max_bytes=150)


def test_token_on_lookalike_host_is_not_sent():
    seen = []

    def fake(url, headers, timeout):
        seen.append(headers)
        return Response(200, {}, iter([b"{}"]))

    GitHub(TOKEN, fake).get_json("https://api.github.com.evil.example/x")
    assert "Authorization" not in seen[0]
