"""Opt-in automatic updates: find, download and verify the newest GitHub release.

Run by the platform wrappers (``auto-update.sh`` on Linux, ``auto-update.ps1`` on Windows) with the bot's
own virtual environment, as a script and in isolated mode::

    python -I twitchbot/updater.py --install-dir DIR --token-file FILE check
    python -I twitchbot/updater.py --install-dir DIR --token-file FILE download --dest EMPTY_DIR

``check`` only reports. ``download`` fetches the platform's release archive and ``SHA256SUMS.txt``,
verifies the checksum, extracts the archive into ``--dest`` and prints the extracted release folder on
the last line of standard output, and writes the release notes to ``--dest``/update-notice.json; the
wrapper copies that notice into the bot's data folder and runs the release's installer in upgrade mode.

Exit codes: 0 = a newer release is available (and, for ``download``, ready), 3 = already up to date,
1 = error. Standard library only: this file must not import the rest of the bot.

The repository is private, so a GitHub token (fine-grained, read-only *Contents*) is read from
``--token-file``. It is sent to ``api.github.com`` only, never to the host GitHub redirects asset
downloads to, and is never logged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

log = logging.getLogger("twitchbot.updater")

REPO = "dxcufgb/TwitchDiscordBot"
API_HOST = "api.github.com"
LATEST_URL = f"https://{API_HOST}/repos/{REPO}/releases/latest"
NAME = "twitchdiscordbot"
CHECKSUMS = "SHA256SUMS.txt"
USER_AGENT = "TwitchDiscordBot-updater"
TIMEOUT = 60
MAX_JSON_BYTES = 5 * 1024 * 1024
MAX_ASSET_BYTES = 200 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_NOTES_CHARS = 100_000
NOTICE_FILE = "update-notice.json"
CHUNK = 64 * 1024

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UP_TO_DATE = 3

_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)$")
_SUM_RE = re.compile(r"^([0-9a-fA-F]{64}) [ *](.+)$")


class UpdateError(Exception):
    """Something went wrong; the message is safe to log (never contains the token)."""


# ---------------------------------------------------------------------------------------------- versions


def parse_version(text: str) -> tuple[int, ...] | None:
    """``"v2.10.0"`` -> ``(2, 10, 0)``. Pre-releases (``2.1.0-rc1``) and anything else -> ``None``."""
    match = _VERSION_RE.match(text.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(candidate: str, installed: str) -> bool:
    """True only if ``candidate`` is a final release strictly newer than ``installed``."""
    new, old = parse_version(candidate), parse_version(installed)
    if new is None or old is None:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def installed_version(install_dir: Path) -> str:
    """The installed version: ``VERSION`` (written by the installers), else ``twitchbot/__init__.py``."""
    version_file = install_dir / "VERSION"
    if version_file.is_file():
        version = version_file.read_text(encoding="utf-8").strip()
        if parse_version(version):
            return version
    init = install_dir / "twitchbot" / "__init__.py"
    if init.is_file():
        match = re.search(r'^__version__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8"), re.M)
        if match and parse_version(match.group(1)):
            return match.group(1)
    raise UpdateError(f"Cannot tell which version is installed in {install_dir} (no usable VERSION file)")


def read_token(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise UpdateError(f"Cannot read the GitHub token file {path} ({exc.strerror or type(exc).__name__})") from None
    if not token or any(ch.isspace() for ch in token):
        raise UpdateError(f"The GitHub token file {path} is empty or malformed")
    return token


# ---------------------------------------------------------------------------------------------- HTTP


@dataclass
class Response:
    status: int
    headers: dict[str, str]  # lower-case names
    chunks: Iterator[bytes]


# (url, headers, timeout) -> Response. Must NOT follow redirects; tests replace it with a fake.
Transport = Callable[[str, dict[str, str], float], Response]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # urlopen then raises HTTPError for the 3xx, which we follow by hand


def urllib_transport(url: str, headers: dict[str, str], timeout: float) -> Response:
    opener = urllib.request.build_opener(_NoRedirect())
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        resp = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        body = exc.read(4096) if exc.fp else b""
        exc.close()
        return Response(exc.code, {k.lower(): v for k, v in exc.headers.items()}, iter([body]))
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise UpdateError(f"Could not reach {urllib.parse.urlsplit(url).hostname}: {reason}") from None

    def chunks() -> Iterator[bytes]:
        with resp:
            while chunk := resp.read(CHUNK):
                yield chunk

    return Response(resp.status, {k.lower(): v for k, v in resp.headers.items()}, chunks())


class GitHub:
    def __init__(self, token: str, transport: Transport = urllib_transport, timeout: float = TIMEOUT) -> None:
        self._token = token
        self._transport = transport
        self._timeout = timeout

    def _open(self, url: str, accept: str) -> Response:
        """GET ``url``, following redirects by hand. The token only ever goes to api.github.com over HTTPS."""
        for _ in range(MAX_REDIRECTS + 1):
            parts = urllib.parse.urlsplit(url)
            if parts.scheme != "https" or not parts.hostname:
                raise UpdateError(f"Refusing to download from a non-HTTPS URL ({parts.scheme}://{parts.hostname})")
            headers = {"Accept": accept, "User-Agent": USER_AGENT}
            if parts.hostname.lower() == API_HOST and parts.port in (None, 443):
                headers["Authorization"] = f"Bearer {self._token}"
                headers["X-GitHub-Api-Version"] = "2022-11-28"
            resp = self._transport(url, headers, self._timeout)
            if resp.status in (301, 302, 303, 307, 308):
                location = resp.headers.get("location")
                if not location:
                    raise UpdateError(f"HTTP {resp.status} from {parts.hostname} without a Location header")
                url = urllib.parse.urljoin(url, location)
                continue
            if resp.status == 200:
                return resp
            raise UpdateError(_http_error(resp.status, parts.hostname))
        raise UpdateError("Too many redirects")

    def get_json(self, url: str) -> dict:
        data = b"".join(_capped(self._open(url, "application/vnd.github+json").chunks, MAX_JSON_BYTES))
        try:
            value = json.loads(data)
        except ValueError:
            raise UpdateError(f"GitHub returned invalid JSON for {url}") from None
        if not isinstance(value, dict):
            raise UpdateError(f"Unexpected answer from GitHub for {url}")
        return value

    def download(self, url: str, dest: Path, max_bytes: int = MAX_ASSET_BYTES) -> str:
        """Save a release asset (API URL) to ``dest``; returns its SHA-256 hex digest."""
        digest = hashlib.sha256()
        with dest.open("xb") as f:
            for chunk in _capped(self._open(url, "application/octet-stream").chunks, max_bytes):
                digest.update(chunk)
                f.write(chunk)
        return digest.hexdigest()


def _capped(chunks: Iterator[bytes], limit: int) -> Iterator[bytes]:
    total = 0
    for chunk in chunks:
        total += len(chunk)
        if total > limit:
            raise UpdateError(f"Download is larger than the {limit:,}-byte limit")
        yield chunk


def _http_error(status: int, host: str) -> str:
    if host == API_HOST and status == 401:
        return "GitHub rejected the token (HTTP 401). Create a new one and enable auto-update again."
    if host == API_HOST and status == 404:
        return (
            f"GitHub returned 404: {REPO} has no published release yet, or the token cannot read it "
            "(it needs read-only Contents access to that repository)."
        )
    if host == API_HOST and status in (403, 429):
        return f"GitHub refused the request (HTTP {status}; rate limit, or the token lacks Contents access)."
    return f"HTTP {status} from {host}"


# ---------------------------------------------------------------------------------------------- releases


@dataclass(frozen=True)
class Release:
    tag: str
    version: str
    assets: dict[str, str]  # asset name -> API download URL
    name: str = ""
    body: str = ""  # release notes (Markdown)
    html_url: str = ""


def latest_release(gh: GitHub) -> Release | None:
    """The latest final release, or None if GitHub's latest is a draft/pre-release or has an odd tag."""
    data = gh.get_json(LATEST_URL)
    tag = str(data.get("tag_name") or "")
    if data.get("draft") or data.get("prerelease"):
        log.info("Latest release %s is a draft or pre-release; ignoring it", tag)
        return None
    if parse_version(tag) is None:
        log.info("Ignoring release with unexpected tag %r", tag)
        return None
    assets = {}
    for asset in data.get("assets") or []:
        if isinstance(asset, dict) and asset.get("name") and asset.get("url"):
            assets[str(asset["name"])] = str(asset["url"])
    return Release(
        tag=tag,
        version=tag.removeprefix("v"),
        assets=assets,
        name=str(data.get("name") or ""),
        body=str(data.get("body") or "")[:MAX_NOTES_CHARS],
        html_url=str(data.get("html_url") or ""),
    )


def asset_name(version: str, platform: str) -> str:
    if platform == "windows":
        return f"{NAME}-{version}-windows.zip"
    if platform == "linux":
        return f"{NAME}-{version}-linux.tar.gz"
    raise UpdateError(f"Unknown platform {platform!r}")


def parse_checksums(text: str) -> dict[str, str]:
    """``sha256sum`` output -> {file name: lower-case hex digest}."""
    sums = {}
    for line in text.splitlines():
        match = _SUM_RE.match(line.strip())
        if match:
            sums[match.group(2).strip()] = match.group(1).lower()
    return sums


# ---------------------------------------------------------------------------------------------- extraction


def _safe_member(name: str, prefix: str) -> PurePosixPath:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != prefix:
        raise UpdateError(f"Unsafe path in the release archive: {name!r}")
    if re.match(r"^[A-Za-z]:", name):
        raise UpdateError(f"Unsafe path in the release archive: {name!r}")
    return path


def extract(archive: Path, dest: Path, prefix: str) -> Path:
    """Extract a release archive into the (empty) ``dest``. Only regular files and folders under ``prefix/``."""
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                _safe_member(info.filename, prefix)
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise UpdateError(f"Symbolic link in the release archive: {info.filename!r}")
            zf.extractall(dest)
    else:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                _safe_member(member.name, prefix)
                if not (member.isfile() or member.isdir()):
                    raise UpdateError(f"Unsupported entry in the release archive: {member.name!r}")
            if hasattr(tarfile, "data_filter"):
                tar.extractall(dest, members=members, filter="data")
            else:  # pragma: no cover - Python builds without the tarfile filter backport
                tar.extractall(dest, members=members)  # every member was checked above
    root = dest / prefix
    if not (root / "Main.py").is_file() or not (root / "twitchbot").is_dir():
        raise UpdateError("The release archive does not contain the bot")
    return root


# ---------------------------------------------------------------------------------------------- commands


def find_update(gh: GitHub, install_dir: Path) -> tuple[str, Release | None]:
    current = installed_version(install_dir)
    release = latest_release(gh)
    if release is None or not is_newer(release.version, current):
        latest = release.version if release else "none"
        log.info("Installed version %s is up to date (latest release: %s)", current, latest)
        return current, None
    log.info("Update available: %s -> %s", current, release.version)
    return current, release


def download_release(gh: GitHub, release: Release, platform: str, dest: Path) -> Path:
    """Download, verify and extract ``release`` into ``dest``; returns the extracted release folder."""
    name = asset_name(release.version, platform)
    if name not in release.assets:
        raise UpdateError(f"Release {release.tag} has no {name}")
    if CHECKSUMS not in release.assets:
        raise UpdateError(f"Release {release.tag} has no {CHECKSUMS}; refusing to install an unverified download")
    dest.mkdir(parents=True, exist_ok=True)
    if any(dest.iterdir()):
        raise UpdateError(f"{dest} must be empty")
    downloads = dest / "download"
    downloads.mkdir()

    sums_file = downloads / CHECKSUMS
    gh.download(release.assets[CHECKSUMS], sums_file, max_bytes=1024 * 1024)
    expected = parse_checksums(sums_file.read_text(encoding="utf-8", errors="replace")).get(name)
    if not expected:
        raise UpdateError(f"{CHECKSUMS} of {release.tag} has no entry for {name}")

    archive = downloads / name
    log.info("Downloading %s", name)
    actual = gh.download(release.assets[name], archive)
    if actual != expected:
        archive.unlink(missing_ok=True)
        raise UpdateError(f"Checksum mismatch for {name}: expected {expected}, got {actual}")
    log.info("SHA-256 verified (%s)", actual)

    root = extract(archive, dest / "release", f"{NAME}-{release.version}")
    installer = root / ("install.ps1" if platform == "windows" else "install.sh")
    if not installer.is_file():
        raise UpdateError(f"The release archive has no {installer.name}")
    shutil.rmtree(downloads, ignore_errors=True)
    write_notice(release, dest / NOTICE_FILE)
    return root


def write_notice(release: Release, path: Path) -> None:
    """Release notes for the bot, which DMs server admins about the update (no secrets in here).

    The wrapper copies it to the bot's ``data/`` folder before running the installer.
    """
    url = release.html_url
    if not url.startswith(f"https://github.com/{REPO}/"):
        url = f"https://github.com/{REPO}/releases/tag/{release.tag}"
    notice = {"version": release.version, "name": release.name, "body": release.body, "html_url": url}
    path.write_text(json.dumps(notice, indent=2), encoding="utf-8")


def default_platform() -> str:
    return "windows" if sys.platform == "win32" else "linux"


def main(argv: list[str] | None = None, transport: Transport = urllib_transport) -> int:
    parser = argparse.ArgumentParser(description="Check for and download TwitchDiscordBot releases.")
    parser.add_argument("--install-dir", type=Path, required=True, help="the bot's installation folder")
    parser.add_argument("--token-file", type=Path, required=True, help="file holding the GitHub token")
    parser.add_argument("--platform", choices=("linux", "windows"), default=default_platform())
    parser.add_argument("--log-file", type=Path, help="also append log lines to this file")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="only report whether a newer release exists")
    download = sub.add_parser("download", help="download, verify and extract a newer release")
    download.add_argument("--dest", type=Path, required=True, help="empty folder to extract the release into")
    args = parser.parse_args(argv)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False

    try:
        gh = GitHub(read_token(args.token_file), transport)
        _, release = find_update(gh, args.install_dir.resolve())
        if release is None:
            return EXIT_UP_TO_DATE
        if args.command == "download":
            root = download_release(gh, release, args.platform, args.dest)
            print(os.fspath(root))
        return EXIT_OK
    except UpdateError as exc:
        log.error("%s", exc)
        return EXIT_ERROR
    except Exception:
        log.exception("Unexpected error while checking for updates")
        return EXIT_ERROR
    finally:
        for handler in handlers:
            log.removeHandler(handler)
            handler.close()


if __name__ == "__main__":
    sys.exit(main())
