import json

import pytest

from twitchbot.storage import StateStore, TrackedStream, migrate_legacy, normalize_login


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Dxcufgb", "dxcufgb"),
        ("  some_user ", "some_user"),
        ("https://www.twitch.tv/SomeUser", "someuser"),
        ("twitch.tv/someuser/", "someuser"),
        ("--name foo", None),
        ("bad name", None),
        ("", None),
        ("a" * 26, None),
    ],
)
def test_normalize_login(raw, expected):
    assert normalize_login(raw) == expected


def test_roundtrip_and_atomic_save(tmp_path):
    store = StateStore(tmp_path / "data" / "state.json")
    guild = store.guild(1)
    guild.channel_id = 10
    guild.streams["foo"] = TrackedStream(login="foo", user_id="99", filters=["Speedrun"], notified=True)
    store.save()

    assert not list(store.path.parent.glob("*.tmp"))
    loaded = StateStore(store.path)
    assert loaded.load()
    stream = loaded.guilds[1].streams["foo"]
    assert loaded.guilds[1].channel_id == 10
    assert (stream.user_id, stream.filters, stream.notified, stream.role_name) == (
        "99",
        ["Speedrun"],
        True,
        "TW-foo-Role",
    )


def test_load_returns_false_without_file(tmp_path):
    assert StateStore(tmp_path / "missing.json").load() is False


def test_rename_login(tmp_path):
    store = StateStore(tmp_path / "s.json")
    store.guild(1).streams["old"] = TrackedStream(login="old", user_id="1")
    store.guild(2).streams["old"] = TrackedStream(login="old", user_id="1")
    store.rename_login("old", "new")
    for gid in (1, 2):
        assert list(store.guilds[gid].streams) == ["new"]
        stream = store.guilds[gid].streams["new"]
        assert stream.login == "new"
        assert stream.role_name == "TW-old-Role"  # existing role keeps working


def test_migrate_legacy(tmp_path):
    (tmp_path / "Streams.json").write_text(
        json.dumps({"Foo": {"URL": "https://www.twitch.tv/Foo", "Role": "TW-Foo-Role", "LastStatus": "live"}})
    )
    (tmp_path / "111-Stream.json").write_text(
        json.dumps({"Foo": {"LastStatus": "live", "filter": ["speedrun", ""]}, "bad name": {}})
    )
    (tmp_path / "222-Stream.json").write_text("{}")
    (tmp_path / "333-Stream.json").write_text("not json")
    # The 1.x bot wrote .env without newlines between keys; dotenv then yields a mangled value.
    legacy_env = {"TOKEN": "x", "STREAMS-CHANNEL-111": "555", "STREAMS-CHANNEL-222": "666STREAMS-CHANNEL-333=777"}

    store = StateStore(tmp_path / "state.json")
    assert migrate_legacy(store, tmp_path, legacy_env)

    assert store.guilds[111].channel_id == 555
    assert store.guilds[222].channel_id is None  # corrupted value is ignored, admin re-runs /setup channel
    stream = store.guilds[111].streams["foo"]
    assert stream.role_name == "TW-Foo-Role"
    assert stream.filters == ["speedrun"]
    assert stream.notified is True
    assert stream.user_id is None  # resolved from Twitch on first poll
    assert "bad name" not in store.guilds[111].streams


def test_migrate_lowercase_streams_file(tmp_path):
    (tmp_path / "streams.json").write_text(json.dumps({"bar": {"Role": "custom"}}))
    (tmp_path / "1-Stream.json").write_text(json.dumps({"bar": {"LastStatus": ""}}))
    store = StateStore(tmp_path / "state.json")
    assert migrate_legacy(store, tmp_path, {})
    assert store.guilds[1].streams["bar"].role_name == "custom"


def test_migrate_nothing(tmp_path):
    assert migrate_legacy(StateStore(tmp_path / "s.json"), tmp_path, {}) is False
