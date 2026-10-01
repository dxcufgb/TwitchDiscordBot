"""Drive the polling loop with a fake Twitch client (no network)."""

from types import SimpleNamespace

from twitchbot.bot import TwitchNotifierBot
from twitchbot.config import Config
from twitchbot.storage import TrackedStream


class FakeTwitch:
    def __init__(self, users, streams):
        self.users = users  # login -> id
        self.streams = streams  # list of dicts
        self.stream_calls = 0

    async def get_users(self, logins=None, user_ids=None):
        for login in logins or []:
            if login in self.users:
                yield SimpleNamespace(id=self.users[login], login=login, display_name=login.title(), description="")

    async def get_streams(self, user_id=None, first=20, stream_type=None):
        self.stream_calls += 1
        assert len(user_id) <= 100
        for s in self.streams:
            if s["user_id"] in user_id:
                yield SimpleNamespace(title="t", game_name="g", thumbnail_url="", viewer_count=1, started_at=None, **s)


def make_bot(tmp_path):
    config = Config("tok", "id", "secret", tmp_path, tmp_path, "INFO", 60, None, None, tmp_path / ".env", {})
    bot = TwitchNotifierBot(config)
    bot.announced = []

    async def fake_announce(a):
        bot.announced.append((a.guild_id, a.stream.login))
        return True

    bot._announce = fake_announce
    return bot


async def test_poll_resolves_ids_renames_and_announces(tmp_path):
    bot = make_bot(tmp_path)
    g = bot.store.guild(1)
    g.channel_id = 5
    g.streams["oldname"] = TrackedStream(login="oldname")  # migrated entry, no user id yet
    g.streams["gone"] = TrackedStream(login="gone")  # banned / unknown user
    bot.twitch = FakeTwitch({"oldname": "7"}, [{"user_id": "7", "user_login": "newname", "user_name": "NewName"}])

    await bot._poll_once()
    assert bot.announced == [(1, "newname")]
    assert set(g.streams) == {"newname", "gone"}
    assert g.streams["newname"].notified is True
    assert bot.store.path.exists()

    await bot._poll_once()  # still live: no duplicate
    assert bot.announced == [(1, "newname")]

    bot.twitch.streams = []  # offline, then live again
    await bot._poll_once()
    bot.twitch.streams = [{"user_id": "7", "user_login": "newname", "user_name": "NewName"}]
    await bot._poll_once()
    assert bot.announced == [(1, "newname"), (1, "newname")]


async def test_poll_batches_requests(tmp_path):
    bot = make_bot(tmp_path)
    g = bot.store.guild(1)
    for i in range(250):
        g.streams[f"u{i}"] = TrackedStream(login=f"u{i}", user_id=str(i))
    bot.twitch = FakeTwitch({}, [])
    await bot._poll_once()
    assert bot.twitch.stream_calls == 3


async def test_poll_loop_survives_errors(tmp_path):
    bot = make_bot(tmp_path)
    bot.store.guild(1).streams["x"] = TrackedStream(login="x", user_id="1")

    class Broken:
        async def get_streams(self, **kwargs):
            raise RuntimeError("twitch down")
            yield  # pragma: no cover

    bot.twitch = Broken()
    await bot.poll_streams.coro(bot)  # the wrapped loop body must swallow and log


class FakeChannel:
    def __init__(self, fail=None):
        self.sent = []
        self.fail = fail

    async def send(self, content, **kwargs):
        if self.fail:
            raise self.fail
        self.sent.append((content, kwargs))


def fake_guild(channel, roles=()):
    roles = list(roles)
    return SimpleNamespace(
        id=1,
        get_channel=lambda cid: channel,
        get_role=lambda rid: next((r for r in roles if r.id == rid), None),
        roles=roles,
    )


async def test_announce_mentions_role_and_never_here(tmp_path, monkeypatch):
    import discord

    from twitchbot.bot import TwitchNotifierBot as RealBot
    from twitchbot.notify import Announcement, LiveInfo

    monkeypatch.setattr(discord.abc, "Messageable", FakeChannel)
    config = Config("tok", "id", "secret", tmp_path, tmp_path, "INFO", 60, None, None, tmp_path / ".env", {})
    bot = RealBot(config)
    info = LiveInfo("7", "foo", "Foo", "Title", "Chess", "https://x/{width}x{height}.jpg", 3, None)
    stream = TrackedStream(login="foo", user_id="7")

    role = SimpleNamespace(id=99, name="TW-foo-Role", mention="<@&99>")
    channel = FakeChannel()
    monkeypatch.setattr(bot, "get_guild", lambda gid: fake_guild(channel, [role]))
    assert await bot._announce(Announcement(1, 5, stream, info))
    content, kwargs = channel.sent[0]
    assert content.startswith("<@&99> **Foo** is live streaming **Chess**")
    assert kwargs["embed"].image.url.startswith("https://x/1280x720.jpg?t=")
    assert stream.role_id == 99  # resolved by name and remembered

    channel = FakeChannel()
    monkeypatch.setattr(bot, "get_guild", lambda gid: fake_guild(channel, []))
    stream.role_id = None
    assert await bot._announce(Announcement(1, 5, stream, info))
    content, kwargs = channel.sent[0]
    assert "@here" not in content and "role is missing" in content
    assert kwargs["allowed_mentions"].roles is False
