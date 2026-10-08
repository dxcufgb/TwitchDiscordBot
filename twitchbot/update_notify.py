"""After an update: DM the release notes to the admins of every server the bot is in.

On start-up the bot compares its version with the one recorded in ``state.json``. If it is strictly newer, the
new version is recorded first (so an announcement happens at most once, even if sending crashes) and one DM is
sent per person: the owner and, with the Server Members intent, every administrator of each server, listing the
servers it applies to.

The notes come from ``data/update-notice.json`` (written by the auto-updater from the GitHub release) or, after a
manual upgrade, from ``CHANGES.md`` shipped in the release archive; otherwise the DM only names the version.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import discord

from .updater import NOTICE_FILE, REPO, is_newer

if TYPE_CHECKING:
    from .storage import StateStore

log = logging.getLogger(__name__)

BUNDLED_NOTES = "CHANGES.md"
EMBED_DESCRIPTION_LIMIT = 4096
EMBED_TOTAL_LIMIT = 6000
FIELD_VALUE_LIMIT = 1024
NOTES_COLOUR = discord.Colour(0x9146FF)


@dataclass(frozen=True)
class ReleaseNotes:
    version: str
    body: str
    url: str


def release_url(version: str) -> str:
    return f"https://github.com/{REPO}/releases/tag/v{version}"


def record_version(store: StateStore, running: str) -> str | None:
    """Record the running version. Returns the previous version if an announcement is due (strict upgrade).

    First run (nothing recorded yet, e.g. a fresh install or an upgrade from a version without this feature)
    and downgrades only record the version.
    """
    previous = store.bot_version
    if previous == running:
        return None
    store.bot_version = running
    store.save()
    if previous is None or not is_newer(running, previous):
        return None
    return previous


def take_notice(data_dir: Path, version: str) -> ReleaseNotes | None:
    """Read and delete ``update-notice.json``; return its notes if they are for ``version``."""
    path = data_dir / NOTICE_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        log.warning("Could not read %s", path)
        return None
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            log.warning("Could not delete %s", path)
    try:
        data = json.loads(raw)
    except ValueError:
        log.warning("Ignoring malformed %s", path)
        return None
    if not isinstance(data, dict) or str(data.get("version", "")).removeprefix("v") != version:
        return None
    url = str(data.get("html_url") or "")
    if not url.startswith(f"https://github.com/{REPO}/"):
        url = release_url(version)
    return ReleaseNotes(version, clean_notes(str(data.get("body") or "")), url)


def bundled_notes(base_dir: Path, version: str) -> ReleaseNotes | None:
    """``CHANGES.md`` from the release archive, if it is for ``version``."""
    try:
        text = (base_dir / BUNDLED_NOTES).read_text(encoding="utf-8")
    except OSError:
        return None
    first, _, rest = text.partition("\n")
    if first.strip() != f"# TwitchDiscordBot {version}":
        return None
    return ReleaseNotes(version, clean_notes(rest), release_url(version))


def clean_notes(body: str) -> str:
    """Drop the install instructions every release starts with; keep the actual changes."""
    body = body.replace("\r\n", "\n")
    body = re.sub(r"(?ms)^## Install\b.*?(?=^## |\Z)", "", body)
    return body.strip()


def collect_recipients(guilds: Iterable[discord.Guild], include_admins: bool) -> dict[int, list[str]]:
    """User id -> names of the servers they own or administer. Bots are skipped; each person appears once."""
    recipients: dict[int, list[str]] = {}

    def add(user_id: int | None, guild: discord.Guild) -> None:
        if user_id is None:
            return
        names = recipients.setdefault(user_id, [])
        if guild.name not in names:
            names.append(guild.name)

    for guild in guilds:
        add(guild.owner_id, guild)
        if include_admins:
            for member in guild.members:
                if not member.bot and member.guild_permissions.administrator:
                    add(member.id, guild)
    return recipients


def _truncate(text: str, limit: int, suffix: str = "…") -> str:
    return text if len(text) <= limit else text[: max(0, limit - len(suffix))] + suffix


def build_update_embed(version: str, notes: ReleaseNotes | None, servers: list[str]) -> discord.Embed:
    url = notes.url if notes else release_url(version)
    title = f"TwitchDiscordBot updated to v{version}"
    footer = "You get this because you own or administer these servers."
    servers_value = _truncate(", ".join(servers) or "-", FIELD_VALUE_LIMIT)
    field_name = "Server" if len(servers) == 1 else "Servers"
    others = len(title) + len(footer) + len(field_name) + len(servers_value)
    budget = min(EMBED_DESCRIPTION_LIMIT, EMBED_TOTAL_LIMIT - others)

    body = notes.body if notes and notes.body else f"No release notes were included. See {url}"
    if len(body) > budget:
        more = f"…\n\n[Full release notes]({url})"
        body = body[: max(0, budget - len(more))].rstrip() + more
    embed = discord.Embed(title=title, url=url, description=body, colour=NOTES_COLOUR)
    embed.add_field(name=field_name, value=servers_value, inline=False)
    embed.set_footer(text=footer)
    return embed


async def send_update_dms(
    recipients: dict[int, list[str]],
    get_user: Callable[[int], Awaitable[discord.abc.User]],
    make_embed: Callable[[list[str]], discord.Embed],
    pause: float = 1.0,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> int:
    """DM each recipient, one after another. Returns how many DMs were sent; failures are logged and skipped."""
    sent = 0
    for index, (user_id, servers) in enumerate(recipients.items()):
        if index:
            await sleep(pause)
        try:
            user = await get_user(user_id)
            await user.send(embed=make_embed(servers), allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            log.info("Could not DM user %s about the update (DMs closed or no shared server)", user_id)
        except discord.NotFound:
            log.info("User %s no longer exists; skipping the update DM", user_id)
        except discord.HTTPException as exc:
            log.warning("Sending the update DM to user %s failed (HTTP %s)", user_id, exc.status)
        except Exception:
            log.exception("Sending the update DM to user %s failed", user_id)
        else:
            sent += 1
    return sent
