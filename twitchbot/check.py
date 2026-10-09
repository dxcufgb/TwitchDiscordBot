"""``python Main.py --check``: verify the configuration and credentials without starting the bot."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import aiohttp
from twitchAPI.twitch import Twitch
from twitchAPI.type import TwitchAuthorizationException

from .config import Config

DISCORD_API = "https://discord.com/api/v10"
DISCORD_ME_URL = f"{DISCORD_API}/users/@me"


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    warning: bool = False  # ok, but worth reading (does not fail the check)


async def check_discord(token: str) -> CheckResult:
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with (
            aiohttp.ClientSession(timeout=timeout, trust_env=True) as session,
            session.get(DISCORD_ME_URL, headers={"Authorization": f"Bot {token}"}) as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                return CheckResult("Discord token", True, f"bot account {data.get('username')} ({data.get('id')})")
            if resp.status == 401:
                return CheckResult("Discord token", False, "rejected by Discord (401); copy the token again")
            return CheckResult("Discord token", False, f"unexpected HTTP {resp.status} from Discord")
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        return CheckResult("Discord token", False, f"could not reach Discord: {exc}")


async def check_twitch(app_id: str, app_secret: str) -> CheckResult:
    # Never include the exception text: twitchAPI's messages contain the request URL with the client secret.
    try:
        twitch = await Twitch(app_id, app_secret)
    except TwitchAuthorizationException:
        return CheckResult(
            "Twitch app credentials", False, "rejected by Twitch (or Twitch unreachable); check Client ID and secret"
        )
    except Exception as exc:
        return CheckResult("Twitch app credentials", False, f"could not reach Twitch ({type(exc).__name__})")
    await twitch.close()
    return CheckResult("Twitch app credentials", True, "app access token issued")


MEMBERS_INTENT_FLAGS = (1 << 14) | (1 << 15)  # GATEWAY_GUILD_MEMBERS, GATEWAY_GUILD_MEMBERS_LIMITED


MEMBERS_INTENT_NAME = "Server Members Intent"
MEMBERS_INTENT_OFF = (
    "turned off, so update notices go to server owners only (the bot starts without it). To reach server admins "
    "too, turn it on: https://discord.com/developers/applications -> your app -> Bot -> Privileged Gateway Intents. "
    "Or set UPDATE_NOTIFY=owner to silence this."
)


def members_intent_result(flags: int) -> CheckResult:
    """Only checked with UPDATE_NOTIFY=admins. Missing is a warning: the bot falls back to owner-only notices."""
    if flags & MEMBERS_INTENT_FLAGS:
        return CheckResult(MEMBERS_INTENT_NAME, True, "enabled")
    return CheckResult(MEMBERS_INTENT_NAME, True, MEMBERS_INTENT_OFF, warning=True)


async def check_members_intent(token: str) -> CheckResult:
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with (
            aiohttp.ClientSession(timeout=timeout, trust_env=True) as session,
            session.get(f"{DISCORD_API}/applications/@me", headers={"Authorization": f"Bot {token}"}) as resp,
        ):
            if resp.status != 200:
                return CheckResult(MEMBERS_INTENT_NAME, True, f"could not check (HTTP {resp.status})", warning=True)
            flags = int((await resp.json()).get("flags") or 0)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, AttributeError) as exc:
        return CheckResult(MEMBERS_INTENT_NAME, True, f"could not check ({type(exc).__name__})", warning=True)
    return members_intent_result(flags)


async def check_slash_commands(token: str) -> CheckResult:
    """Informational: how many slash commands Discord has for the bot, plus the invite link with both scopes."""
    from .bot import invite_url

    name = "Slash commands"
    headers = {"Authorization": f"Bot {token}"}
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True, headers=headers) as session:
            async with session.get(f"{DISCORD_API}/oauth2/applications/@me") as resp:
                if resp.status != 200:
                    return CheckResult(name, True, f"could not read the application (HTTP {resp.status})")
                app_id = int((await resp.json())["id"])
            async with session.get(f"{DISCORD_API}/applications/{app_id}/commands") as resp:
                count = len(await resp.json()) if resp.status == 200 else None
    except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, ValueError) as exc:
        return CheckResult(name, True, f"could not check ({type(exc).__name__})")
    registered = (
        "none registered yet (the bot registers them when it starts)"
        if not count
        else f"{count} registered"
        if count is not None
        else "count unavailable"
    )
    return CheckResult(
        name,
        True,
        f"{registered}. If they don't show up in your server, re-invite the bot with: {invite_url(app_id)}",
    )


async def run_checks(config: Config) -> list[CheckResult]:
    results = list(
        await asyncio.gather(
            check_discord(config.discord_token),
            check_twitch(config.twitch_app_id, config.twitch_app_secret),
        )
    )
    if results[0].ok:
        if config.update_notify == "admins":
            results.append(await check_members_intent(config.discord_token))
        results.append(await check_slash_commands(config.discord_token))
    return results


def print_results(results: list[CheckResult]) -> bool:
    for result in results:
        status = "FAIL" if not result.ok else "WARN" if result.warning else "OK"
        print(f"[{status}] {result.name}: {result.detail}")
    return all(r.ok for r in results)
