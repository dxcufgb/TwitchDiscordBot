"""Registering the slash commands with Discord, reliably, at every start and from the installers.

Discord keeps whatever command set was registered last, so an update only shows its new commands once they
are registered again. ``sync_commands`` does that: it registers the running version's commands in the
current scope (``DEV_GUILD_ID``'s server, or globally), retries after a failure, and empties the set
registered in the previous scope when ``DEV_GUILD_ID`` was added, changed or removed; otherwise Discord
keeps showing the stale commands next to the new ones. What was registered is recorded in ``state.json``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING

import aiohttp
import discord

if TYPE_CHECKING:
    from .bot import TwitchNotifierBot

log = logging.getLogger(__name__)

RETRY_DELAYS = (10, 30, 90)  # seconds to wait before each new attempt after a failure
# Network trouble and Discord server errors are worth another try; other HTTP errors (403, 404, 400) are not.
TRANSIENT_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError, OSError)


def _scope_name(guild_id: int | None) -> str:
    return f"in server {guild_id}" if guild_id else "globally"


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, discord.HTTPException):
        return exc.status >= 500
    return isinstance(exc, TRANSIENT_ERRORS)


def commands_fingerprint(bot: TwitchNotifierBot, guild: discord.abc.Snowflake | None) -> str:
    """Hash of the command definitions that would be registered in ``guild`` (None = global)."""
    payload = sorted((cmd.to_dict(bot.tree) for cmd in bot.tree.get_commands(guild=guild)), key=lambda c: c["name"])
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


async def sync_commands(
    bot: TwitchNotifierBot,
    *,
    delays: Sequence[float] = RETRY_DELAYS,
    persist: bool = True,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> bool:
    """Register the commands; returns True once they are registered and the old scope is cleaned up.

    Never raises (except cancellation). ``persist=False`` (the installers' ``--sync-commands``) leaves
    ``state.json`` alone: the bot records the registration itself when it starts.
    """
    guild_id = bot.config.dev_guild_id
    guild = discord.Object(id=guild_id) if guild_id else None
    if guild is not None:
        bot.tree.copy_global_to(guild=guild)
    fingerprint = commands_fingerprint(bot, guild)
    previous = bot.store.commands or {}
    if "guild_id" in previous:
        old_guild_id = previous["guild_id"]
        stale_scope = old_guild_id != guild_id
    else:
        # Nothing recorded yet (first start, or state written by an older version): in server mode, empty the global set
        # in case one was registered earlier; in global mode the earlier server, if any, is unknown.
        old_guild_id = None
        stale_scope = guild_id is not None
    registered = cleaned = False

    attempts = len(delays) + 1
    for attempt in range(attempts):
        if attempt:
            delay = delays[attempt - 1]
            log.warning(
                "Retrying the slash command registration in %d s (attempt %d of %d)", delay, attempt + 1, attempts
            )
            await sleep(delay)
        step = "register the slash commands " + _scope_name(guild_id)
        try:
            if not registered:
                synced = await bot.tree.sync(guild=guild)
                registered = True
                changed = previous.get("hash") != fingerprint or stale_scope
                log.info(
                    "Registered %d slash command(s) %s (%s)",
                    len(synced),
                    _scope_name(guild_id),
                    "changed since the last registration" if changed else "unchanged",
                )
            if stale_scope and not cleaned:
                step = "remove the old slash commands " + _scope_name(old_guild_id)
                try:
                    await _clear_scope(bot, old_guild_id)
                    log.info("Removed the slash commands registered earlier %s", _scope_name(old_guild_id))
                except discord.HTTPException as exc:
                    if _is_transient(exc):
                        raise
                    # E.g. the bot left that server or lost access to it: there is nothing it can remove there.
                    log.warning("Could not %s (%s); skipping it", step, exc)
                cleaned = True
        except discord.ClientException as exc:  # e.g. the application id is unknown; retrying won't help
            log.error("Could not %s: %s", step, exc)
            return False
        except Exception as exc:
            if not _is_transient(exc):
                log.error("Could not %s: %s", step, exc)
                return False
            log.warning("Could not %s: %s", step, exc)
            continue

        if persist:
            bot.store.commands = {"guild_id": guild_id, "hash": fingerprint}
            try:
                bot.store.save()
            except OSError:
                log.exception("Could not record the slash command registration in %s", bot.store.path)
        return True

    log.error(
        "Gave up on the slash command registration after %d attempts; Discord keeps showing the previous "
        "commands until the bot registers them at its next start",
        attempts,
    )
    return False


async def _clear_scope(bot: TwitchNotifierBot, guild_id: int | None) -> None:
    """Empty the command set registered in ``guild_id`` (None = global) without touching the local tree."""
    app_id = bot.application_id
    if app_id is None:
        raise discord.ClientException("the application id is not known yet")
    if guild_id:
        await bot.http.bulk_upsert_guild_commands(app_id, guild_id, [])
    else:
        await bot.http.bulk_upsert_global_commands(app_id, [])
