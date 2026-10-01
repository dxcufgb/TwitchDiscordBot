from twitchbot.notify import (
    LiveInfo,
    chunked,
    plan_announcements,
    split_message,
    strip_discord_invites,
    title_matches,
)
from twitchbot.storage import StateStore, TrackedStream


def live(user_id="1", title="Speedrun any%", login="foo"):
    return LiveInfo(user_id, login, login.title(), title, "Game", "", 5, None)


def make_store(tmp_path, **stream_kwargs):
    store = StateStore(tmp_path / "s.json")
    guild = store.guild(1)
    guild.channel_id = 10
    guild.streams["foo"] = TrackedStream(login="foo", user_id="1", **stream_kwargs)
    return store


def test_title_matches():
    assert title_matches([], "anything")
    assert title_matches(["SPEEDRUN"], "speedrun today")
    assert title_matches(["x", "run"], "Speedrun")
    assert not title_matches(["casual"], "Speedrun")
    assert not title_matches(["a"], None)


def test_strip_discord_invites():
    text = "Hi! Join https://discord.gg/abcDEF12 or discord.com/invite/xyz and www.discordapp.com/invite/q1"
    assert strip_discord_invites(text) == "Hi! Join or and"
    assert strip_discord_invites("line1\nline2") == "line1\nline2"
    assert strip_discord_invites(None) == ""


def test_chunked():
    assert [list(c) for c in chunked([1, 2, 3, 4, 5], 2)] == [[1, 2], [3, 4], [5]]


def test_split_message():
    assert split_message([]) == []
    assert split_message(["a", "b"], limit=10) == ["a\nb"]
    assert split_message(["aaaa", "bbbb", "cccc"], limit=9) == ["aaaa\nbbbb", "cccc"]
    assert all(len(m) <= 5 for m in split_message(["x" * 20], limit=5))


def test_announces_once(tmp_path):
    store = make_store(tmp_path)
    announcements, _ = plan_announcements(store, {"1": live()})
    assert len(announcements) == 1
    assert announcements[0].channel_id == 10
    announcements[0].stream.notified = True  # what the bot does after sending
    assert plan_announcements(store, {"1": live()})[0] == []


def test_offline_resets(tmp_path):
    store = make_store(tmp_path, notified=True)
    announcements, changed = plan_announcements(store, {})
    assert announcements == [] and changed
    assert store.guilds[1].streams["foo"].notified is False


def test_filter_waits_for_matching_title(tmp_path):
    store = make_store(tmp_path, filters=["speedrun"])
    assert plan_announcements(store, {"1": live(title="Just chatting")})[0] == []
    assert len(plan_announcements(store, {"1": live(title="SPEEDRUN time")})[0]) == 1


def test_no_channel_no_announcement(tmp_path):
    store = make_store(tmp_path)
    store.guilds[1].channel_id = None
    assert plan_announcements(store, {"1": live()})[0] == []


def test_guilds_are_independent(tmp_path):
    """Regression: the 1.x bot shared one dict between global and guild state."""
    store = make_store(tmp_path)
    store.guild(2).channel_id = 20
    store.guild(2).streams["foo"] = TrackedStream(login="foo", user_id="1", notified=True)
    announcements, _ = plan_announcements(store, {"1": live()})
    assert [a.guild_id for a in announcements] == [1]
