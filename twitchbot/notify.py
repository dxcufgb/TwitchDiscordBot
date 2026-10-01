"""Pure (Discord/Twitch independent) logic for deciding what to announce."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

from .storage import StateStore, TrackedStream

T = TypeVar("T")

INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord\.gg|discord(?:app)?\.com/invite)/[A-Za-z0-9-]+",
    re.IGNORECASE,
)


def strip_discord_invites(text: str) -> str:
    """Remove Discord invite links (we don't advertise other servers) and tidy whitespace."""
    text = INVITE_RE.sub("", text or "")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def title_matches(filters: Iterable[str], title: str) -> bool:
    """No filters means everything matches; otherwise any filter must appear (case-insensitive)."""
    filters = [f.casefold() for f in filters if f]
    if not filters:
        return True
    title = (title or "").casefold()
    return any(f in title for f in filters)


def chunked(items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


@dataclass(frozen=True)
class LiveInfo:
    user_id: str
    login: str
    display_name: str
    title: str
    game_name: str
    thumbnail_url: str
    viewer_count: int
    started_at: datetime | None


@dataclass(frozen=True)
class Announcement:
    guild_id: int
    channel_id: int
    stream: TrackedStream
    info: LiveInfo


def plan_announcements(store: StateStore, live: dict[str, LiveInfo]) -> tuple[list[Announcement], bool]:
    """Work out which guilds must be notified, and reset streams that went offline.

    Returns ``(announcements, state_changed)``. A stream's ``notified`` flag is only
    set by the caller once the announcement has actually been handled, so a stream
    whose title doesn't match a filter yet is re-checked on every poll (and announced
    if the streamer later changes the title).
    """
    announcements: list[Announcement] = []
    changed = False
    for guild_id, guild in store.guilds.items():
        for stream in guild.streams.values():
            if stream.user_id is None:
                continue
            info = live.get(stream.user_id)
            if info is None:
                if stream.notified:
                    stream.notified = False
                    changed = True
                continue
            if stream.display_name != info.display_name:
                stream.display_name = info.display_name
                changed = True
            if stream.notified or guild.channel_id is None:
                continue
            if title_matches(stream.filters, info.title):
                announcements.append(Announcement(guild_id, guild.channel_id, stream, info))
    return announcements, changed


def split_message(lines: Iterable[str], limit: int = 2000) -> list[str]:
    """Join lines into as few messages as possible without exceeding Discord's length limit."""
    messages: list[str] = []
    current = ""
    for line in lines:
        line = line[:limit]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            messages.append(current)
            current = line
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages
