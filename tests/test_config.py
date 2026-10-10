import pytest

from twitchbot.config import ConfigError, load_config

KEYS = (
    "DISCORD_TOKEN",
    "TOKEN",
    "TWITCH_APP_ID",
    "TWITCH-APP-ID",
    "TWITCH_APP_SECRET",
    "TWITCH-APP-SECRET",
    "POLL_INTERVAL",
    "OWNER_ID",
    "DATA_DIR",
    "UPDATE_NOTIFY",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)


def test_legacy_names(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TOKEN=abc\r\nTWITCH-APP-ID=id \nTWITCH-APP-SECRET=sec=ret\nSTREAMS-CHANNEL-1=2\n")
    config = load_config(env)
    assert (config.discord_token, config.twitch_app_id, config.twitch_app_secret) == ("abc", "id", "sec=ret")
    assert config.legacy_env["STREAMS-CHANNEL-1"] == "2"
    assert config.poll_interval == 60
    assert config.data_dir.name == "data" and config.data_dir.is_absolute()


def test_new_names_and_env_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DISCORD_TOKEN=file\nTWITCH_APP_ID=i\nTWITCH_APP_SECRET=s\nPOLL_INTERVAL=30\nOWNER_ID=42\n")
    monkeypatch.setenv("DISCORD_TOKEN", "fromenv")
    config = load_config(env)
    assert config.discord_token == "fromenv"
    assert config.poll_interval == 30
    assert config.owner_id == 42


def test_missing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TOKEN=abc\n")
    with pytest.raises(ConfigError, match="TWITCH_APP_ID"):
        load_config(env)


def test_bad_interval(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TOKEN=a\nTWITCH_APP_ID=i\nTWITCH_APP_SECRET=s\nPOLL_INTERVAL=5\n")
    with pytest.raises(ConfigError):
        load_config(env)


def test_update_notify_settings(tmp_path):
    env = tmp_path / ".env"
    env.write_text("DISCORD_TOKEN=t\nTWITCH_APP_ID=i\nTWITCH_APP_SECRET=s\n")
    assert load_config(env).update_notify == "admins"
    env.write_text("DISCORD_TOKEN=t\nTWITCH_APP_ID=i\nTWITCH_APP_SECRET=s\nUPDATE_NOTIFY=Owner\n")
    assert load_config(env).update_notify == "owner"


def test_update_notify_is_validated(tmp_path):
    env = tmp_path / ".env"
    env.write_text("DISCORD_TOKEN=t\nTWITCH_APP_ID=i\nTWITCH_APP_SECRET=s\nUPDATE_NOTIFY=everyone\n")
    with pytest.raises(ConfigError):
        load_config(env)
