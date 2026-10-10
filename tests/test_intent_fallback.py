"""Start-up when Discord refuses the Server Members intent (UPDATE_NOTIFY=admins): fall back to owner-only notices."""

from __future__ import annotations

import logging

import discord
import pytest

from twitchbot import bot as bot_module
from twitchbot.bot import TwitchNotifierBot, run_bot
from twitchbot.config import Config


def make_config(tmp_path, update_notify="admins"):
    return Config(
        "tok", "id", "secret", tmp_path, tmp_path, "INFO", 60, None, None, tmp_path / ".env", {}, update_notify
    )


class FakeTwitch:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_discord(monkeypatch):
    """Replace the gateway connection: a client asking for the members intent is refused, like Discord does."""
    seen = {"bots": [], "twitch": [], "refuse_all": False}

    async def fake_twitch(app_id, app_secret):
        assert all(t.closed for t in seen["twitch"]), "a second Twitch client while the first is still open"
        client = FakeTwitch()
        seen["twitch"].append(client)
        return client

    async def fake_start(self, token, *, reconnect=True):
        seen["bots"].append(self)
        self.twitch = await bot_module.Twitch(self.config.twitch_app_id, self.config.twitch_app_secret)
        if self.intents.members or seen["refuse_all"]:
            raise discord.PrivilegedIntentsRequired(None)
        self.exit_code = 75

    monkeypatch.setattr(bot_module, "Twitch", fake_twitch)
    monkeypatch.setattr(TwitchNotifierBot, "start", fake_start)
    return seen


def test_admins_mode_falls_back_to_owner_only(tmp_path, fake_discord, caplog):
    caplog.set_level(logging.WARNING)
    bot = run_bot(make_config(tmp_path))

    first, second = fake_discord["bots"]
    assert bot is second and bot.exit_code == 75
    assert first.intents.members and first.is_closed() and first.twitch is None
    assert not second.intents.members and second.config.update_notify == "owner"
    assert first.config.update_notify == "admins"  # frozen config, replaced rather than changed
    assert [t.closed for t in fake_discord["twitch"]] == [True, True]

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "server owners only" in message and "UPDATE_NOTIFY=owner" in message and "Privileged Gateway" in message
    assert "tok" not in message.split()  # the token never reaches the log


def test_owner_mode_never_asks_for_the_intent(tmp_path, fake_discord):
    bot = run_bot(make_config(tmp_path, "owner"))
    assert len(fake_discord["bots"]) == 1 and not bot.intents.members and bot.exit_code == 75


def test_refusal_without_members_intent_is_fatal(tmp_path, fake_discord):
    fake_discord["refuse_all"] = True
    with pytest.raises(discord.PrivilegedIntentsRequired):
        run_bot(make_config(tmp_path, "off"))
    assert len(fake_discord["bots"]) == 1


def test_failing_fallback_is_fatal(tmp_path, fake_discord, monkeypatch, caplog):
    fake_discord["refuse_all"] = True
    monkeypatch.setattr(bot_module, "load_config", lambda: make_config(tmp_path))
    monkeypatch.setattr(bot_module, "setup_logging", lambda config: None)
    assert bot_module.main([]) == 2
    assert len(fake_discord["bots"]) == 2
    assert all(b.is_closed() for b in fake_discord["bots"])
    assert all(t.closed for t in fake_discord["twitch"])
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)


async def test_close_is_safe_to_call_twice(tmp_path):
    bot = TwitchNotifierBot(make_config(tmp_path))
    twitch = FakeTwitch()
    bot.twitch = twitch
    await bot.close()
    await bot.close()
    assert twitch.closed and bot.twitch is None and bot.is_closed()
