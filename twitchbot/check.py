"""``python Main.py --check``: verify the configuration and credentials without starting the bot."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import aiohttp
from twitchAPI.twitch import Twitch
from twitchAPI.type import TwitchAuthorizationException

from .config import Config

DISCORD_ME_URL = "https://discord.com/api/v10/users/@me"


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


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


async def run_checks(config: Config) -> list[CheckResult]:
    return list(
        await asyncio.gather(
            check_discord(config.discord_token),
            check_twitch(config.twitch_app_id, config.twitch_app_secret),
        )
    )


def print_results(results: list[CheckResult]) -> bool:
    for result in results:
        print(f"[{'OK' if result.ok else 'FAIL'}] {result.name}: {result.detail}")
    return all(r.ok for r in results)
