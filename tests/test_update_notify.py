"""Update DMs to server admins (twitchbot/update_notify.py), with fakes: no network."""

from __future__ import annotations

import json
import logging
from functools import partial
from types import SimpleNamespace

import discord
import pytest

from twitchbot import update_notify as un
from twitchbot.storage import StateStore


def store_with(tmp_path, recorded):
    store = StateStore(tmp_path / "state.json")
    store.bot_version = recorded
    return store


# ------------------------------------------------------------------------------------------ version detection


def test_first_run_records_without_announcing(tmp_path):
    store = store_with(tmp_path, None)
    assert un.record_version(store, "2.2.0") is None
    reloaded = StateStore(store.path)
    reloaded.load()
    assert reloaded.bot_version == "2.2.0"


def test_upgrade_is_announced_once(tmp_path):
    store = store_with(tmp_path, "2.1.1")
    assert un.record_version(store, "2.2.0") == "2.1.1"
    assert store.bot_version == "2.2.0" and store.path.exists()  # recorded before sending: at most once
    assert un.record_version(store, "2.2.0") is None


@pytest.mark.parametrize("recorded", ["2.2.0", "3.0.0"])
def test_same_or_older_version_is_not_announced(tmp_path, recorded):
    store = store_with(tmp_path, recorded)
    assert un.record_version(store, "2.2.0") is None
    assert store.bot_version == "2.2.0"


def test_old_state_file_without_bot_version_loads(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 2, "guilds": {"1": {"channel_id": 5, "streams": {}}}}))
    store = StateStore(path)
    assert store.load()
    assert store.bot_version is None and store.guilds[1].channel_id == 5
    store.bot_version = "2.2.0"
    store.save()
    data = json.loads(path.read_text())
    assert (data["version"], data["bot_version"]) == (3, "2.2.0")


# ------------------------------------------------------------------------------------------ release notes


def write_notice(tmp_path, **fields):
    notice = {"version": "2.2.0", "name": "x", "body": "## What's Changed\n- fix", "html_url": ""}
    notice.update(fields)
    (tmp_path / "update-notice.json").write_text(json.dumps(notice))


def test_notice_matching_version_is_used_and_deleted(tmp_path):
    url = "https://github.com/dxcufgb/TwitchDiscordBot/releases/tag/v2.2.0"
    write_notice(tmp_path, body="## Install\n\nsteps\n\n## What's Changed\n- fix", html_url=url)
    notes = un.take_notice(tmp_path, "2.2.0")
    assert notes == un.ReleaseNotes("2.2.0", "## What's Changed\n- fix", url)
    assert not (tmp_path / "update-notice.json").exists()


def test_notice_for_another_version_is_ignored_but_deleted(tmp_path):
    write_notice(tmp_path, version="2.3.0")
    assert un.take_notice(tmp_path, "2.2.0") is None
    assert not (tmp_path / "update-notice.json").exists()


def test_notice_url_must_point_to_the_repository(tmp_path):
    write_notice(tmp_path, html_url="https://evil.example/")
    assert un.take_notice(tmp_path, "2.2.0").url == un.release_url("2.2.0")


def test_malformed_or_missing_notice(tmp_path):
    assert un.take_notice(tmp_path, "2.2.0") is None
    (tmp_path / "update-notice.json").write_text("{not json")
    assert un.take_notice(tmp_path, "2.2.0") is None
    assert not (tmp_path / "update-notice.json").exists()


def test_bundled_notes(tmp_path):
    (tmp_path / "CHANGES.md").write_text("# TwitchDiscordBot 2.2.0\n\nChanges since v2.1.1:\n\n- a\n")
    assert un.bundled_notes(tmp_path, "2.2.0").body == "Changes since v2.1.1:\n\n- a"
    assert un.bundled_notes(tmp_path, "2.3.0") is None  # stale file from an older release
    assert un.bundled_notes(tmp_path / "missing", "2.2.0") is None


# ------------------------------------------------------------------------------------------ recipients


def member(user_id, admin=False, bot=False):
    return SimpleNamespace(id=user_id, bot=bot, guild_permissions=discord.Permissions(administrator=admin))


def guild(name, owner_id, members=()):
    return SimpleNamespace(name=name, owner_id=owner_id, members=list(members))


GUILDS = [
    guild("A", 1, [member(1, admin=True), member(2, admin=True), member(3), member(9, admin=True, bot=True)]),
    guild("B", 2, [member(2, admin=True), member(1, admin=True)]),
    guild("C", 4),
]


def test_recipients_with_members_intent_are_deduplicated():
    assert un.collect_recipients(GUILDS, include_admins=True) == {1: ["A", "B"], 2: ["A", "B"], 4: ["C"]}


def test_recipients_without_members_intent_are_owners_only():
    assert un.collect_recipients(GUILDS, include_admins=False) == {1: ["A"], 2: ["B"], 4: ["C"]}


# ------------------------------------------------------------------------------------------ embed


def embed_size(embed: discord.Embed) -> int:
    return len(embed)


def test_short_notes_are_kept():
    notes = un.ReleaseNotes("2.2.0", "- fix", un.release_url("2.2.0"))
    embed = un.build_update_embed("2.2.0", notes, ["A", "B"])
    assert embed.title == "TwitchDiscordBot updated to v2.2.0"
    assert embed.description == "- fix"
    assert embed.fields[0].name == "Servers" and embed.fields[0].value == "A, B"


def test_long_notes_are_truncated_with_link():
    notes = un.ReleaseNotes("2.2.0", "x" * 10_000, un.release_url("2.2.0"))
    embed = un.build_update_embed("2.2.0", notes, ["S" * 50] * 40)
    assert len(embed.description) <= un.EMBED_DESCRIPTION_LIMIT
    assert embed_size(embed) <= un.EMBED_TOTAL_LIMIT
    assert len(embed.fields[0].value) <= un.FIELD_VALUE_LIMIT
    assert embed.description.endswith(f"[Full release notes]({un.release_url('2.2.0')})")


def test_no_notes_names_the_version():
    embed = un.build_update_embed("2.2.0", None, ["A"])
    assert un.release_url("2.2.0") in embed.description
    assert embed.fields[0].name == "Server"


# ------------------------------------------------------------------------------------------ sending


class FakeUser:
    def __init__(self, error=None):
        self.error = error
        self.sent = []

    async def send(self, **kwargs):
        if self.error:
            raise self.error
        self.sent.append(kwargs)


def http_error(cls, status):
    return cls(SimpleNamespace(status=status, reason="x"), "boom")


async def test_send_continues_after_errors(caplog):
    users = {
        1: FakeUser(http_error(discord.Forbidden, 403)),
        2: FakeUser(http_error(discord.HTTPException, 500)),
        3: FakeUser(RuntimeError("x")),
        4: FakeUser(),
    }
    pauses = []

    async def get_user(user_id):
        return users[user_id]

    async def sleep(seconds):
        pauses.append(seconds)

    recipients = {1: ["A"], 2: ["A"], 3: ["B"], 4: ["A", "B"]}
    with caplog.at_level(logging.INFO):
        sent = await un.send_update_dms(
            recipients, get_user, lambda servers: un.build_update_embed("2.2.0", None, servers), sleep=sleep
        )
    assert sent == 1
    assert pauses == [1.0, 1.0, 1.0]  # sequential, with a pause between DMs
    (message,) = users[4].sent
    assert message["embed"].fields[0].value == "A, B"
    mentions = message["allowed_mentions"]
    assert not (mentions.everyone or mentions.users or mentions.roles)
    assert "DMs closed" in caplog.text and "HTTP 500" in caplog.text


@pytest.mark.parametrize(("mode", "expected"), [("admins", {1, 2}), ("owner", {1}), ("off", set())])
async def test_bot_announce_update_end_to_end(tmp_path, monkeypatch, mode, expected):
    """The bot's on_ready path: records the version, uses the notice, DMs according to UPDATE_NOTIFY."""
    from twitchbot import bot as bot_module
    from twitchbot.bot import TwitchNotifierBot
    from twitchbot.config import Config

    config = Config(
        "tok", "id", "secret", tmp_path, tmp_path, "INFO", 60, None, None, tmp_path / ".env", {}, update_notify=mode
    )
    bot = TwitchNotifierBot(config)
    assert bot.intents.members == (mode == "admins")  # only admins needs the privileged intent
    bot.store.bot_version = "1.0.0"
    write_notice(tmp_path, version=bot_module.__version__, body="- new stuff")
    users = {1: FakeUser(), 2: FakeUser(), 3: FakeUser()}
    monkeypatch.setattr(TwitchNotifierBot, "guilds", [guild("A", 1, [member(2, admin=True), member(3)])])
    monkeypatch.setattr(bot, "get_user", lambda user_id: None)

    async def fetch_user(user_id):
        return users[user_id]

    async def no_sleep(_):
        return None

    monkeypatch.setattr(bot, "fetch_user", fetch_user)
    monkeypatch.setattr(bot_module, "send_update_dms", partial(un.send_update_dms, sleep=no_sleep))
    await bot.announce_update()
    assert bot.store.bot_version == bot_module.__version__
    assert {uid for uid, u in users.items() if u.sent} == expected
    for uid in expected:
        assert users[uid].sent[0]["embed"].description == "- new stuff"
    assert not (tmp_path / "update-notice.json").exists()

    await bot.announce_update()  # a second start: nothing more
    assert all(len(u.sent) <= 1 for u in users.values())
