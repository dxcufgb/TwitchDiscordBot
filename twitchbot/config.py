"""Configuration loaded from environment variables / the .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent.parent
UPDATE_NOTIFY_CHOICES = ("admins", "owner", "off")


def _first(env: dict[str, str | None], *names: str) -> str | None:
    """Return the first non-empty value among ``names`` (new name first, legacy names after)."""
    for name in names:
        value = env.get(name)
        if value and value.strip():
            return value.strip()
    return None


@dataclass(frozen=True)
class Config:
    discord_token: str
    twitch_app_id: str
    twitch_app_secret: str
    data_dir: Path
    log_dir: Path
    log_level: str
    poll_interval: int
    owner_id: int | None
    dev_guild_id: int | None
    env_file: Path
    # Raw .env contents, used once to migrate legacy STREAMS-CHANNEL-<guild> entries.
    legacy_env: dict[str, str]
    # Who gets a DM after the bot was updated: "admins" (owner + administrators), "owner" or "off".
    update_notify: str = "admins"


class ConfigError(RuntimeError):
    pass


def load_config(env_file: Path | None = None) -> Config:
    env_file = env_file or BASE_DIR / ".env"
    file_values = {k: v for k, v in dotenv_values(env_file).items() if v is not None} if env_file.exists() else {}
    # Real environment variables win over the .env file.
    env: dict[str, str | None] = {**file_values, **os.environ}

    token = _first(env, "DISCORD_TOKEN", "TOKEN")
    app_id = _first(env, "TWITCH_APP_ID", "TWITCH-APP-ID")
    app_secret = _first(env, "TWITCH_APP_SECRET", "TWITCH-APP-SECRET")
    missing = [
        name
        for name, value in (
            ("DISCORD_TOKEN", token),
            ("TWITCH_APP_ID", app_id),
            ("TWITCH_APP_SECRET", app_secret),
        )
        if not value
    ]
    if missing:
        raise ConfigError(f"Missing required setting(s) in {env_file} or environment: {', '.join(missing)}")

    def _int(name: str, default: int | None) -> int | None:
        raw = _first(env, name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc

    poll_interval = _int("POLL_INTERVAL", 60) or 60
    if poll_interval < 15:
        raise ConfigError("POLL_INTERVAL must be at least 15 seconds")

    update_notify = (_first(env, "UPDATE_NOTIFY") or "admins").lower()
    if update_notify not in UPDATE_NOTIFY_CHOICES:
        raise ConfigError(f"UPDATE_NOTIFY must be one of {', '.join(UPDATE_NOTIFY_CHOICES)}, got {update_notify!r}")

    data_dir = Path(_first(env, "DATA_DIR") or BASE_DIR / "data")
    log_dir = Path(_first(env, "LOG_DIR") or BASE_DIR / "logs")
    if not data_dir.is_absolute():
        data_dir = BASE_DIR / data_dir
    if not log_dir.is_absolute():
        log_dir = BASE_DIR / log_dir

    return Config(
        discord_token=token,  # type: ignore[arg-type]
        twitch_app_id=app_id,  # type: ignore[arg-type]
        twitch_app_secret=app_secret,  # type: ignore[arg-type]
        data_dir=data_dir,
        log_dir=log_dir,
        log_level=(_first(env, "LOG_LEVEL") or "INFO").upper(),
        poll_interval=poll_interval,
        owner_id=_int("OWNER_ID", None),
        dev_guild_id=_int("DEV_GUILD_ID", None),
        env_file=env_file,
        legacy_env={k: v for k, v in file_values.items()},
        update_notify=update_notify,
    )
