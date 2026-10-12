"""Registering the slash commands: retries, scope changes and the record in state.json."""

import json
from types import SimpleNamespace

import aiohttp
import discord
import pytest

from twitchbot import bot as bot_module
from twitchbot.bot import TwitchNotifierBot, main
from twitchbot.command_sync import RETRY_DELAYS, sync_commands
from twitchbot.commands import register_commands
from twitchbot.config import Config


def make_bot(tmp_path, guild_id=None):
    config = Config("tok", "id", "secret", tmp_path, tmp_path, "INFO", 60, None, guild_id, tmp_path / ".env", {})
    bot = TwitchNotifierBot(config)
    bot._connection.application_id = 4242
    register_commands(bot)
    return bot


def http_error(status, cls=discord.HTTPException):
    return cls(SimpleNamespace(status=status, reason="x"), {"code": 0, "message": "error"})


class FakeDiscord:
    """Records tree.sync / bulk upserts; ``failures`` are raised by the next calls, in order."""

    def __init__(self, bot, monkeypatch, failures=()):
        self.failures = list(failures)
        self.synced = []  # scopes the tree was synced to (None = global)
        self.cleared = []  # scopes emptied through the HTTP API
        self.sleeps = []

        async def sync(*, guild=None):
            self._maybe_fail()
            self.synced.append(guild.id if guild else None)
            return bot.tree.get_commands(guild=guild)

        async def bulk_global(app_id, payload):
            assert payload == []
            self._maybe_fail()
            self.cleared.append(None)

        async def bulk_guild(app_id, guild_id, payload):
            assert payload == []
            self._maybe_fail()
            self.cleared.append(guild_id)

        monkeypatch.setattr(bot.tree, "sync", sync)
        monkeypatch.setattr(bot.http, "bulk_upsert_global_commands", bulk_global)
        monkeypatch.setattr(bot.http, "bulk_upsert_guild_commands", bulk_guild)

    def _maybe_fail(self):
        if self.failures:
            raise self.failures.pop(0)

    async def sleep(self, seconds):
        self.sleeps.append(seconds)


async def test_registers_globally_and_records_it(tmp_path, monkeypatch, caplog):
    bot = make_bot(tmp_path)
    fake = FakeDiscord(bot, monkeypatch)
    caplog.set_level("INFO")
    assert await sync_commands(bot, sleep=fake.sleep)
    assert fake.synced == [None] and fake.cleared == [] and fake.sleeps == []
    record = json.loads(bot.store.path.read_text())["commands"]
    assert record["guild_id"] is None and len(record["hash"]) == 64
    assert "changed since the last registration" in caplog.text

    # The next start with the same commands logs that nothing changed.
    caplog.clear()
    again = make_bot(tmp_path)
    again.store.load()
    FakeDiscord(again, monkeypatch)
    assert await sync_commands(again, sleep=fake.sleep)
    assert "(unchanged)" in caplog.text


async def test_transient_failures_are_retried(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)
    fake = FakeDiscord(bot, monkeypatch, [aiohttp.ClientConnectionError("down"), http_error(503)])
    assert await sync_commands(bot, sleep=fake.sleep)
    assert fake.sleeps == list(RETRY_DELAYS[:2])
    assert fake.synced == [None]
    assert bot.store.commands is not None


async def test_gives_up_after_the_last_retry(tmp_path, monkeypatch, caplog):
    bot = make_bot(tmp_path)
    fake = FakeDiscord(bot, monkeypatch, [TimeoutError()] * 10)
    assert not await sync_commands(bot, sleep=fake.sleep)
    assert fake.sleeps == list(RETRY_DELAYS)
    assert bot.store.commands is None and not bot.store.path.exists()
    assert "Gave up on the slash command registration" in caplog.text


async def test_client_errors_are_not_retried(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)
    fake = FakeDiscord(bot, monkeypatch, [http_error(403, discord.Forbidden)])
    assert not await sync_commands(bot, sleep=fake.sleep)
    assert fake.sleeps == [] and bot.store.commands is None


async def test_switching_to_a_server_removes_the_global_commands(tmp_path, monkeypatch):
    bot = make_bot(tmp_path, guild_id=77)
    bot.store.commands = {"guild_id": None, "hash": "old"}
    fake = FakeDiscord(bot, monkeypatch)
    assert await sync_commands(bot, sleep=fake.sleep)
    assert fake.synced == [77] and fake.cleared == [None]
    assert {c.name for c in bot.tree.get_commands(guild=discord.Object(77))} >= {"setup", "stream"}
    assert bot.store.commands["guild_id"] == 77


async def test_server_mode_without_a_record_removes_global_commands(tmp_path, monkeypatch):
    bot = make_bot(tmp_path, guild_id=77)
    fake = FakeDiscord(bot, monkeypatch)
    assert await sync_commands(bot, sleep=fake.sleep)
    assert fake.cleared == [None]


async def test_changed_or_removed_server_is_emptied(tmp_path, monkeypatch):
    bot = make_bot(tmp_path, guild_id=None)
    bot.store.commands = {"guild_id": 55, "hash": "old"}
    fake = FakeDiscord(bot, monkeypatch)
    assert await sync_commands(bot, sleep=fake.sleep)
    assert fake.synced == [None] and fake.cleared == [55]

    bot = make_bot(tmp_path, guild_id=66)
    bot.store.commands = {"guild_id": 55, "hash": "old"}
    fake = FakeDiscord(bot, monkeypatch)
    assert await sync_commands(bot, sleep=fake.sleep)
    assert fake.synced == [66] and fake.cleared == [55]


async def test_same_scope_cleans_nothing(tmp_path, monkeypatch):
    bot = make_bot(tmp_path, guild_id=None)
    bot.store.commands = {"guild_id": None, "hash": "old"}
    fake = FakeDiscord(bot, monkeypatch)
    assert await sync_commands(bot, sleep=fake.sleep)
    assert fake.cleared == []


async def test_failed_cleanup_is_retried_without_registering_again(tmp_path, monkeypatch):
    bot = make_bot(tmp_path, guild_id=None)
    bot.store.commands = {"guild_id": 55, "hash": "old"}
    fake = FakeDiscord(bot, monkeypatch)
    calls = []
    original = bot.http.bulk_upsert_guild_commands

    async def flaky(app_id, guild_id, payload):
        calls.append(guild_id)
        if len(calls) == 1:
            raise http_error(502)
        await original(app_id, guild_id, payload)

    monkeypatch.setattr(bot.http, "bulk_upsert_guild_commands", flaky)
    assert await sync_commands(bot, sleep=fake.sleep)
    assert fake.synced == [None] and calls == [55, 55] and fake.sleeps == [RETRY_DELAYS[0]]
    assert bot.store.commands["guild_id"] is None


async def test_cleanup_in_a_server_the_bot_left_is_skipped(tmp_path, monkeypatch, caplog):
    bot = make_bot(tmp_path, guild_id=None)
    bot.store.commands = {"guild_id": 55, "hash": "old"}
    fake = FakeDiscord(bot, monkeypatch)

    async def gone(app_id, guild_id, payload):
        raise http_error(403, discord.Forbidden)

    monkeypatch.setattr(bot.http, "bulk_upsert_guild_commands", gone)
    assert await sync_commands(bot, sleep=fake.sleep)
    assert fake.sleeps == [] and bot.store.commands["guild_id"] is None
    assert "skipping it" in caplog.text


async def test_persist_false_leaves_the_state_file_alone(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)
    fake = FakeDiscord(bot, monkeypatch)
    assert await sync_commands(bot, persist=False, sleep=fake.sleep)
    assert bot.store.commands is None and not bot.store.path.exists()


@pytest.mark.parametrize("record", [{"guild_id": "55", "hash": "x"}, {"guild_id": True}, ["x"], "x"])
def test_invalid_record_is_ignored(tmp_path, record):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 3, "guilds": {}, "commands": record}))
    bot = make_bot(tmp_path)
    assert bot.store.load()
    assert bot.store.commands is None


def test_sync_commands_option(tmp_path, monkeypatch):
    calls = []

    async def fake_once(config):
        calls.append(config)
        return 0

    monkeypatch.setenv("DISCORD_TOKEN", "tok")
    monkeypatch.setenv("TWITCH_APP_ID", "id")
    monkeypatch.setenv("TWITCH_APP_SECRET", "secret")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(bot_module, "register_commands_once", fake_once)
    assert main(["--sync-commands"]) == 0
    assert len(calls) == 1
    assert not (tmp_path / "logs").exists()  # console only: no log files owned by the installer's account


async def test_register_commands_once_reports_a_rejected_token(tmp_path, monkeypatch):
    config = Config("tok", "id", "secret", tmp_path, tmp_path, "INFO", 60, None, None, tmp_path / ".env", {})

    async def reject(self, token):
        raise discord.LoginFailure("Improper token has been passed.")

    monkeypatch.setattr(bot_module._CommandRegistrar, "login", reject)
    assert await bot_module.register_commands_once(config) == 2
