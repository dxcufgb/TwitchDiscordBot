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
            "name": f"TwitchDiscordBot {version}",
            "body": "## What's Changed\n- things",
            "html_url": "https://github.com/dxcufgb/TwitchDiscordBot/releases/tag/v2.2.0",
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


@pytest.mark.parametrize(
    ("status", "headers", "text"),
    [
        (404, {}, "no published release"),
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1893456000"}, "rate limit is used up"),
        (429, {"retry-after": "60"}, "Try again in 60 seconds"),
        (403, {}, "refused the request"),
    ],
)
def test_http_errors_are_explained(install, capsys, status, headers, text):
    def fake(url, headers_, timeout):
        return Response(status, headers, iter([b"{}"]))

    assert run(install, fake, "check") == updater.EXIT_ERROR
    err = capsys.readouterr().err
    assert text in err
    assert "Traceback" not in err
    assert TOKEN not in err


def test_rate_limit_without_token_is_a_clear_error(install, capsys):
    folder, _ = install

    def fake(url, headers, timeout):
        return Response(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1893456000"}, iter([b"{}"]))

    assert updater.main(["--install-dir", str(folder), "--platform", "linux", "check"], fake) == updater.EXIT_ERROR
    err = capsys.readouterr().err
    assert "rate limit is used up" in err and "60 requests an hour" in err and "2030-01-01 00:00 UTC" in err
    assert "Traceback" not in err


def test_rejected_token_falls_back_to_no_token(install, capsys):
    # An expired or revoked token must not stop the updates: the repository is public.
    fake = FakeGitHub()
    real = fake.__call__

    def rejecting(url, headers, timeout):
        if "Authorization" in headers:
            fake.calls.append((url, dict(headers)))
            return Response(401, {}, iter([b"{}"]))
        return real(url, headers, timeout)

    assert run(install, rejecting, "check") == updater.EXIT_OK
    assert [("Authorization" in headers) for _, headers in fake.calls] == [True, False]
    err = capsys.readouterr().err
    assert "rejected the token" in err and TOKEN not in err


def test_unauthenticated_401_is_an_error(install, capsys):
    folder, _ = install

    def fake(url, headers, timeout):
        return Response(401, {}, iter([b"{}"]))

    assert updater.main(["--install-dir", str(folder), "check"], fake) == updater.EXIT_ERROR
    assert "HTTP 401" in capsys.readouterr().err


def test_missing_token_file(tmp_path, capsys):
    code = updater.main(["--install-dir", str(tmp_path), "--token-file", str(tmp_path / "nope"), "check"], FakeGitHub())
    assert code == updater.EXIT_ERROR
    assert "Cannot read the GitHub token file" in capsys.readouterr().err


def test_works_without_a_token(install, tmp_path, capsys):
    # The repository is public: no --token-file, no Authorization header anywhere.
    folder, _ = install
    fake = FakeGitHub()
    args = ["--install-dir", str(folder), "--platform", "linux", "download", "--dest", str(tmp_path / "work")]
    assert updater.main(args, fake) == updater.EXIT_OK
    assert fake.calls and all("Authorization" not in headers for _, headers in fake.calls)
    assert any(url.startswith(CDN) for url, _ in fake.calls)
    assert any(url.startswith(API) and headers.get("X-GitHub-Api-Version") for url, headers in fake.calls)
    root = Path(capsys.readouterr().out.strip().splitlines()[-1])
    assert (root / "install.sh").is_file()


def test_token_is_used_when_configured(install):
    fake = FakeGitHub()
    assert run(install, fake, "check") == updater.EXIT_OK
    assert fake.calls[0][1]["Authorization"] == f"Bearer {TOKEN}"


def test_github_client_without_token_sends_no_authorization():
    seen = []

    def fake(url, headers, timeout):
        seen.append(headers)
        return Response(200, {}, iter([b"{}"]))

    GitHub(None, fake).get_json(f"{API}/releases/latest")
    GitHub("", fake).get_json(f"{API}/releases/latest")
    assert all("Authorization" not in headers for headers in seen)


def test_token_optional_marker_is_present():
    # auto-update.sh greps the installed updater for this line before calling it without --token-file.
    text = Path(updater.__file__).read_text(encoding="utf-8")
    assert "\nTOKEN_OPTIONAL = True\n" in text


# ------------------------------------------------------------------------------------------- download


def test_download_verifies_and_extracts(install, tmp_path, capsys):
    fake = FakeGitHub()
    dest = tmp_path / "work"
    assert run(install, fake, "download", "--dest", str(dest)) == updater.EXIT_OK
    root = Path(capsys.readouterr().out.strip().splitlines()[-1])
    assert root == dest / "release" / "twitchdiscordbot-2.2.0"
    assert (root / "install.sh").is_file() and (root / "Main.py").is_file()
    assert not (dest / "download").exists()
    notice = json.loads((dest / "update-notice.json").read_text())
    assert notice == {
        "version": "2.2.0",
        "name": "TwitchDiscordBot 2.2.0",
        "body": "## What's Changed\n- things",
        "html_url": "https://github.com/dxcufgb/TwitchDiscordBot/releases/tag/v2.2.0",
    }


def test_release_with_unified_names_uses_the_versioned_archive(install, tmp_path, capsys):
    # Releases now also carry fixed-name copies, the setup wizard and legacy names, all listed in SHA256SUMS.txt.
    # The updater keeps downloading <name>-X.Y.Z-linux.tar.gz and verifying it against SHA256SUMS.txt.
    fake = FakeGitHub()
    extra = [
        "twitchdiscordbot-linux.tar.gz",
        "twitchdiscordbot-2.2.0-setup.exe",
        "twitchdiscordbot-2.2.0-windows-setup.exe",
    ]
    for number, name in enumerate(extra, 10):
        fake.release["assets"].insert(0, {"name": name, "url": f"{API}/releases/assets/{number}"})
        fake.files[f"{CDN}/{number}"] = b"not the archive"
    sums = fake.files[f"{CDN}/2"].decode()
    sums += "".join(f"{hashlib.sha256(b'not the archive').hexdigest()}  {name}\n" for name in extra)
    fake.files[f"{CDN}/2"] = sums.encode()
    assert run(install, fake, "download", "--dest", str(tmp_path / "work")) == updater.EXIT_OK
    fetched = {url.rsplit("/", 1)[1] for url, _ in fake.calls if "/releases/assets/" in url}
    assert fetched == {"1", "2"}


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
