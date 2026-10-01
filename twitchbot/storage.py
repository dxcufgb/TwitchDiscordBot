"""Persistent bot state, stored as one JSON file written atomically.

Layout of ``state.json``::

    {
      "version": 2,
      "guilds": {
        "<guild id>": {
          "channel_id": 123 | null,
          "streams": {
            "<twitch login>": {
              "user_id": "1234" | null,
              "display_name": "Name" | null,
              "role_id": 456 | null,
              "role_name": "TW-name-Role",
              "filters": ["text", ...],
              "notified": false
            }
          }
        }
      }
    }
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

STATE_VERSION = 2
LOGIN_RE = re.compile(r"^[a-z0-9_]{1,25}$")


def normalize_login(value: str) -> str | None:
    """Turn user input (login or channel URL) into a Twitch login, or None if invalid."""
    value = value.strip().lower()
    value = re.sub(r"^(https?://)?(www\.|m\.)?twitch\.tv/", "", value).strip("/@ ")
    return value if LOGIN_RE.fullmatch(value) else None


def default_role_name(login: str) -> str:
    return f"TW-{login}-Role"


@dataclass
class TrackedStream:
    login: str
    user_id: str | None = None
    display_name: str | None = None
    role_id: int | None = None
    role_name: str = ""
    filters: list[str] = field(default_factory=list)
    # True once the current live session has been announced in this guild.
    notified: bool = False

    def __post_init__(self) -> None:
        if not self.role_name:
            self.role_name = default_role_name(self.login)

    @property
    def url(self) -> str:
        return f"https://www.twitch.tv/{self.login}"

    @property
    def name(self) -> str:
        return self.display_name or self.login

    def to_dict(self) -> dict:
        data = asdict(self)
        del data["login"]
        return data

    @classmethod
    def from_dict(cls, login: str, data: dict) -> TrackedStream:
        return cls(
            login=login,
            user_id=data.get("user_id"),
            display_name=data.get("display_name"),
            role_id=data.get("role_id"),
            role_name=data.get("role_name") or "",
            filters=list(data.get("filters") or []),
            notified=bool(data.get("notified", False)),
        )


@dataclass
class GuildState:
    channel_id: int | None = None
    streams: dict[str, TrackedStream] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "streams": {login: s.to_dict() for login, s in sorted(self.streams.items())},
        }

    @classmethod
    def from_dict(cls, data: dict) -> GuildState:
        return cls(
            channel_id=data.get("channel_id"),
            streams={login: TrackedStream.from_dict(login, s) for login, s in (data.get("streams") or {}).items()},
        )


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.guilds: dict[int, GuildState] = {}

    # ---- access -------------------------------------------------------
    def guild(self, guild_id: int) -> GuildState:
        return self.guilds.setdefault(guild_id, GuildState())

    def remove_guild(self, guild_id: int) -> None:
        self.guilds.pop(guild_id, None)

    def iter_streams(self) -> Iterator[tuple[int, TrackedStream]]:
        for guild_id, guild in self.guilds.items():
            for stream in guild.streams.values():
                yield guild_id, stream

    def rename_login(self, old: str, new: str) -> None:
        """A Twitch user changed their login; re-key every guild entry."""
        for guild in self.guilds.values():
            stream = guild.streams.get(old)
            if stream is None or new in guild.streams:
                continue
            del guild.streams[old]
            stream.login = new
            guild.streams[new] = stream

    # ---- persistence --------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "version": STATE_VERSION,
            "guilds": {str(gid): g.to_dict() for gid, g in sorted(self.guilds.items())},
        }

    def load(self) -> bool:
        """Load state from disk. Returns False if no state file exists yet."""
        if not self.path.exists():
            return False
        with self.path.open(encoding="utf-8") as f:
            data = json.load(f)
        self.guilds = {int(gid): GuildState.from_dict(g) for gid, g in (data.get("guilds") or {}).items()}
        return True

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), indent=2, sort_keys=True)
        # Write to a temp file in the same directory, then atomically replace,
        # so a crash or power loss never leaves a half-written state file.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


# ---- migration from the 1.x bot -------------------------------------------

_LEGACY_GUILD_FILE = re.compile(r"^(\d+)-Stream\.json$")
_LEGACY_CHANNEL_KEY = re.compile(r"^STREAMS-CHANNEL-(\d+)$")


def migrate_legacy(store: StateStore, legacy_dir: Path, legacy_env: dict[str, str]) -> bool:
    """Import data written by the old single-file bot. Returns True if anything was imported.

    Legacy files are left untouched so the migration can be redone by deleting state.json.
    """
    imported = False

    global_streams: dict = {}
    for name in ("Streams.json", "streams.json"):
        path = legacy_dir / name
        if path.exists():
            try:
                global_streams = json.loads(path.read_text(encoding="utf-8")) or {}
            except (OSError, ValueError):
                log.exception("Could not read legacy %s", path)
            break

    for key, value in legacy_env.items():
        match = _LEGACY_CHANNEL_KEY.match(key)
        if match and value.strip().isdigit():
            store.guild(int(match.group(1))).channel_id = int(value.strip())
            imported = True

    for path in sorted(legacy_dir.glob("*-Stream.json")):
        match = _LEGACY_GUILD_FILE.match(path.name)
        if not match:
            continue
        try:
            entries = json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            log.exception("Could not read legacy %s", path)
            continue
        if not isinstance(entries, dict):
            continue
        guild = store.guild(int(match.group(1)))
        for raw_key, entry in entries.items():
            login = normalize_login(raw_key)
            if login is None:
                log.warning("Skipping legacy stream with invalid name %r in %s", raw_key, path.name)
                continue
            entry = entry if isinstance(entry, dict) else {}
            legacy_role = (global_streams.get(raw_key) or {}).get("Role") or default_role_name(raw_key.strip())
            guild.streams[login] = TrackedStream(
                login=login,
                role_name=legacy_role,
                filters=[f for f in entry.get("filter", []) if isinstance(f, str) and f.strip()],
                notified=entry.get("LastStatus") == "live",
            )
            imported = True

    return imported
