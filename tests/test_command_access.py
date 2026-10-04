"""Detecting servers where the bot was invited without the applications.commands scope."""

from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import discord
from aiohttp import web

from twitchbot import check
from twitchbot.bot import BOT_PERMISSIONS, TwitchNotifierBot, invite_url
from twitchbot.config import Config


def make_bot(tmp_path):
    config = Config("tok", "id", "secret", tmp_path, tmp_path, "INFO", 60, None, None, tmp_path / ".env", {})
    bot = TwitchNotifierBot(config)
    bot._connection.application_id = 4242
    return bot


def forbidden(code):
    return discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), {"code": code, "message": "Missing Access"}
    )


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, content):
        self.sent.append(content)


def test_invite_url_has_both_scopes():
    query = parse_qs(urlparse(invite_url(4242)).query)
    assert query["client_id"] == ["4242"]
    assert set(query["scope"][0].split()) == {"bot", "applications.commands"}
    assert int(query["permissions"][0]) == BOT_PERMISSIONS.value
    assert BOT_PERMISSIONS.manage_roles and BOT_PERMISSIONS.mention_everyone


async def test_missing_scope_is_reported_once(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)
    guild = SimpleNamespace(id=1, name="Server")
    channel = FakeChannel()
    monkeypatch.setattr(bot, "_notice_channel", lambda g, preferred: channel)

    async def no_access(guild):
        raise forbidden(50001)

    monkeypatch.setattr(bot.tree, "fetch_commands", no_access)
    assert await bot.check_command_access(guild) is False
    assert len(channel.sent) == 1
    assert "applications.commands" in channel.sent[0] and "client_id=4242" in channel.sent[0]
    assert bot.store.guilds[1].commands_warning_sent is True
    assert '"commands_warning_sent": true' in bot.store.path.read_text()

    # Restarting the bot must not post the notice again.
    assert await bot.check_command_access(guild) is False
    assert len(channel.sent) == 1

    # Once an admin re-authorizes, the flag is cleared so a future problem is reported again.
    async def access_ok(guild):
        return []

    monkeypatch.setattr(bot.tree, "fetch_commands", access_ok)
    assert await bot.check_command_access(guild) is True
    assert bot.store.guilds[1].commands_warning_sent is False


async def test_other_errors_are_not_treated_as_missing_scope(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)
    channel = FakeChannel()
    monkeypatch.setattr(bot, "_notice_channel", lambda g, preferred: channel)

    async def other_forbidden(guild):
        raise forbidden(50013)

    monkeypatch.setattr(bot.tree, "fetch_commands", other_forbidden)
    assert await bot.check_command_access(SimpleNamespace(id=2, name="x")) is True
    assert channel.sent == []


async def test_check_reports_commands_and_invite_link(monkeypatch):
    async def app_info(request):
        return web.json_response({"id": "4242"})

    async def commands(request):
        assert request.headers["Authorization"] == "Bot tok"
        return web.json_response([{"name": "setup"}, {"name": "help"}])

    app = web.Application()
    app.router.add_get("/api/v10/oauth2/applications/@me", app_info)
    app.router.add_get("/api/v10/applications/4242/commands", commands)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(check, "DISCORD_API", f"http://127.0.0.1:{port}/api/v10")
    try:
        result = await check.check_slash_commands("tok")
    finally:
        await runner.cleanup()
    assert result.ok
    assert "2 registered" in result.detail
    assert "client_id=4242" in result.detail and "applications.commands" in result.detail
