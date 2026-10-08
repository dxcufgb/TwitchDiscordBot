"""Discord client, Twitch polling loop and start-up."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from logging.handlers import RotatingFileHandler

import discord
from discord import app_commands
from discord.ext import tasks
from twitchAPI.helper import first
from twitchAPI.object.api import TwitchUser
from twitchAPI.twitch import Twitch

from . import __version__
from .config import BASE_DIR, Config, ConfigError, load_config
from .notify import Announcement, LiveInfo, chunked, plan_announcements
from .storage import StateStore, TrackedStream, migrate_legacy
from .update_notify import (
    build_update_embed,
    bundled_notes,
    collect_recipients,
    record_version,
    send_update_dms,
    take_notice,
)

log = logging.getLogger("twitchbot")

TWITCH_PURPLE = discord.Colour(0x9146FF)
TWITCH_BATCH = 100  # max ids/logins per Helix request
DISCORD_MISSING_ACCESS = 50001

# Permissions the bot needs; used for the invite link.
BOT_PERMISSIONS = discord.Permissions(
    view_channel=True, send_messages=True, embed_links=True, mention_everyone=True, manage_roles=True
)


def invite_url(application_id: int) -> str:
    """Invite / re-authorize link with both scopes slash commands need."""
    return discord.utils.oauth_url(application_id, permissions=BOT_PERMISSIONS, scopes=("bot", "applications.commands"))


MEMBERS_INTENT_HELP = (
    'Discord refused the connection because the bot\'s "Server Members Intent" is turned off. Turn it on: '
    "https://discord.com/developers/applications -> your app -> Bot -> Privileged Gateway Intents -> "
    "Server Members Intent, save, then start the bot again."
)

COMMANDS_NOT_AUTHORIZED = (
    "**My slash commands aren't enabled in this server**, so `/setup`, `/stream` and the others don't show up. "
    "This happens when the bot was invited without the `applications.commands` permission (common for bots "
    "invited before 2023).\n"
    "A server admin can fix it by opening this link and picking this server; the bot stays and keeps its "
    "settings:\n<{url}>"
)


class TwitchNotifierBot(discord.Client):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.none()
        intents.guilds = True  # guild/channel/role cache
        # Privileged "Server Members Intent" (enable it in the Developer Portal): finds server admins for update
        # DMs, so it is only requested when UPDATE_NOTIFY=admins.
        intents.members = config.update_notify == "admins"
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.config = config
        self.tree = app_commands.CommandTree(self)
        self.store = StateStore(config.data_dir / "state.json")
        self.twitch: Twitch | None = None
        self.exit_code = 0
        self._warned_unknown: set[str] = set()
        self._checked_command_access = False
        self._update_check_started = False
        self._background: set[asyncio.Task] = set()
        self.poll_streams.change_interval(seconds=config.poll_interval)

    # ---- lifecycle ----------------------------------------------------
    async def setup_hook(self) -> None:
        self._load_state()
        self.twitch = await Twitch(self.config.twitch_app_id, self.config.twitch_app_secret)

        from .commands import register_commands

        register_commands(self)
        await self._sync_commands()
        self.poll_streams.start()

    async def _sync_commands(self) -> None:
        try:
            if self.config.dev_guild_id:
                guild = discord.Object(id=self.config.dev_guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
            else:
                synced = await self.tree.sync()
            log.info("Synced %d slash command(s)", len(synced))
        except discord.HTTPException:
            log.exception("Failed to sync slash commands")
        if self.application_id:
            log.info(
                "Invite / re-authorize link (adds slash commands to a server): %s", invite_url(self.application_id)
            )

    def _load_state(self) -> None:
        if self.store.load():
            log.info("Loaded state for %d guild(s) from %s", len(self.store.guilds), self.store.path)
            return
        if migrate_legacy(self.store, self.config.env_file.parent, self.config.legacy_env):
            log.info("Migrated legacy data for %d guild(s) into %s", len(self.store.guilds), self.store.path)
        self.store.save()

    async def close(self) -> None:
        self.poll_streams.cancel()
        if self.twitch is not None:
            await self.twitch.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info(
            "Logged in as %s (id %s), discord.py %s, bot %s, in %d guild(s)",
            self.user,
            self.user.id if self.user else "?",
            discord.__version__,
            __version__,
            len(self.guilds),
        )
        # on_ready fires again after reconnects; once per process is enough for both checks.
        if not self._update_check_started:
            self._update_check_started = True
            task = asyncio.create_task(self.announce_update())
            self._background.add(task)
            task.add_done_callback(self._background.discard)
        if not self._checked_command_access:
            self._checked_command_access = True
            for guild in list(self.guilds):
                await self.check_command_access(guild)

    async def announce_update(self) -> None:
        """DM server admins once after the bot was updated (see update_notify). Never raises."""
        try:
            notice = take_notice(self.config.data_dir, __version__)  # always consume the notice file
            previous = record_version(self.store, __version__)
            if previous is None:
                return
            log.info("Bot updated from %s to %s", previous, __version__)
            mode = self.config.update_notify
            if mode == "off":
                return
            include_admins = mode == "admins"
            notes = notice or bundled_notes(BASE_DIR, __version__)
            recipients = collect_recipients(self.guilds, include_admins)

            async def get_user(user_id: int) -> discord.abc.User:
                return self.get_user(user_id) or await self.fetch_user(user_id)

            sent = await send_update_dms(
                recipients, get_user, lambda servers: build_update_embed(__version__, notes, servers)
            )
            log.info("Sent the update notice to %d of %d server admin(s)", sent, len(recipients))
        except Exception:
            log.exception("Could not send the update notice")

    async def check_command_access(self, guild: discord.Guild) -> bool:
        """Return False (and tell the server once) if the bot may not use slash commands there.

        Discord answers "Missing Access" (50001) to a command lookup in a server where the bot was
        authorized without the applications.commands scope; global commands are then invisible there.
        """
        try:
            await self.tree.fetch_commands(guild=guild)
        except discord.Forbidden as exc:
            if exc.code != DISCORD_MISSING_ACCESS:
                log.warning("Could not check slash commands in guild %s: %s", guild.id, exc)
                return True
        except discord.HTTPException as exc:
            log.warning("Could not check slash commands in guild %s: %s", guild.id, exc)
            return True
        else:
            state = self.store.guilds.get(guild.id)
            if state is not None and state.commands_warning_sent:
                state.commands_warning_sent = False  # fixed; warn again if it ever breaks
                self.store.save()
            return True

        url = invite_url(self.application_id) if self.application_id else "(application id unknown)"
        log.warning(
            "Slash commands are NOT authorized in guild %s (%s): re-invite the bot with %s", guild.name, guild.id, url
        )
        state = self.store.guild(guild.id)
        if not state.commands_warning_sent:
            channel = self._notice_channel(guild, state.channel_id)
            if channel is not None:
                try:
                    await channel.send(COMMANDS_NOT_AUTHORIZED.format(url=url))
                    state.commands_warning_sent = True
                    self.store.save()
                except discord.HTTPException:
                    log.warning("Could not post the slash command notice in guild %s", guild.id)
        return False

    def _notice_channel(self, guild: discord.Guild, preferred_id: int | None) -> discord.TextChannel | None:
        candidates = [guild.get_channel(preferred_id) if preferred_id else None, guild.system_channel]
        candidates += sorted(guild.text_channels, key=lambda c: c.position)
        for channel in candidates:
            if isinstance(channel, discord.TextChannel) and channel.permissions_for(guild.me).send_messages:
                return channel
        return None

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("Joined guild %s (%s)", guild.name, guild.id)
        if not await self.check_command_access(guild):
            return  # the notice already explains what to do
        channel = guild.system_channel
        if channel and channel.permissions_for(guild.me).send_messages:
            try:
                await channel.send(
                    "Hello! I'm the TwitchBot. An admin can pick the channel for live alerts with "
                    "`/setup channel`, then add streams with `/stream add`. See `/help` for everything else."
                )
            except discord.HTTPException:
                log.warning("Could not send welcome message in %s", guild.id)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        log.info("Removed from guild %s (%s); deleting its settings", guild.name, guild.id)
        self.store.remove_guild(guild.id)
        self.store.save()

    async def is_owner(self, user: discord.abc.User) -> bool:
        if self.config.owner_id:
            return user.id == self.config.owner_id
        app = await self.application_info()
        if app.team:
            return user.id in {member.id for member in app.team.members}
        return user.id == app.owner.id

    # ---- Twitch helpers -------------------------------------------------
    async def fetch_twitch_user(self, login: str) -> TwitchUser | None:
        assert self.twitch is not None
        return await first(self.twitch.get_users(logins=[login]))

    async def _resolve_user_ids(self) -> bool:
        """Fill in Twitch user ids for streams that don't have one yet (new or migrated)."""
        assert self.twitch is not None
        missing = sorted({s.login for _, s in self.store.iter_streams() if s.user_id is None})
        if not missing:
            return False
        found: dict[str, TwitchUser] = {}
        for batch in chunked(missing, TWITCH_BATCH):
            async for user in self.twitch.get_users(logins=list(batch)):
                found[user.login] = user
        for _, stream in self.store.iter_streams():
            user = found.get(stream.login)
            if user is not None:
                stream.user_id = user.id
                stream.display_name = user.display_name
            elif stream.user_id is None and stream.login not in self._warned_unknown:
                self._warned_unknown.add(stream.login)
                log.warning("Twitch user %r not found (renamed or banned?); it won't be tracked", stream.login)
        return bool(found)

    async def _fetch_live(self) -> dict[str, LiveInfo]:
        assert self.twitch is not None
        user_ids = sorted({s.user_id for _, s in self.store.iter_streams() if s.user_id})
        live: dict[str, LiveInfo] = {}
        for batch in chunked(user_ids, TWITCH_BATCH):
            async for s in self.twitch.get_streams(user_id=list(batch), first=TWITCH_BATCH, stream_type="live"):
                live[s.user_id] = LiveInfo(
                    user_id=s.user_id,
                    login=s.user_login,
                    display_name=s.user_name,
                    title=s.title or "",
                    game_name=s.game_name or "",
                    thumbnail_url=s.thumbnail_url or "",
                    viewer_count=s.viewer_count or 0,
                    started_at=s.started_at,
                )
        return live

    # ---- polling loop -----------------------------------------------------
    @tasks.loop(seconds=60)
    async def poll_streams(self) -> None:
        # Never let an exception escape: that would stop the loop for every guild.
        try:
            await self._poll_once()
        except Exception:
            log.exception("Polling Twitch failed; will retry next interval")

    @poll_streams.before_loop
    async def _before_poll(self) -> None:
        await self.wait_until_ready()

    async def _poll_once(self) -> None:
        changed = await self._resolve_user_ids()
        live = await self._fetch_live()

        for info in live.values():
            for _, stream in list(self.store.iter_streams()):
                if stream.user_id == info.user_id and stream.login != info.login:
                    log.info("Twitch user %s renamed %s -> %s", info.user_id, stream.login, info.login)
                    self.store.rename_login(stream.login, info.login)
                    changed = True

        announcements, planned_changes = plan_announcements(self.store, live)
        changed |= planned_changes
        for announcement in announcements:
            try:
                handled = await self._announce(announcement)
            except Exception:
                log.exception("Announcing %s in guild %s failed", announcement.stream.login, announcement.guild_id)
                handled = False
            if handled:
                announcement.stream.notified = True
                changed = True
        if changed:
            self.store.save()

    # ---- announcements ------------------------------------------------------
    def resolve_role(self, guild: discord.Guild, stream: TrackedStream) -> discord.Role | None:
        role = guild.get_role(stream.role_id) if stream.role_id else None
        if role is None:
            role = discord.utils.get(guild.roles, name=stream.role_name)
            if role is not None:
                stream.role_id = role.id
        return role

    async def ensure_role(self, guild: discord.Guild, stream: TrackedStream) -> discord.Role:
        role = self.resolve_role(guild, stream)
        if role is None:
            role = await guild.create_role(
                name=stream.role_name, mentionable=False, reason=f"Live notifications for {stream.login}"
            )
            stream.role_id = role.id
        return role

    async def _announce(self, a: Announcement) -> bool:
        """Post a go-live message. Returns True if handled (sent, or failed permanently)."""
        guild = self.get_guild(a.guild_id)
        if guild is None:
            return False
        channel = guild.get_channel(a.channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            log.warning("Alert channel %s in guild %s is missing; skipping %s", a.channel_id, guild.id, a.stream.login)
            return True

        role = self.resolve_role(guild, a.stream)
        game = f" streaming **{a.info.game_name}**" if a.info.game_name else ""
        content = f"**{a.info.display_name}** is live{game}!"
        if role is not None:
            content = f"{role.mention} {content}"
        else:
            content += "\n-# Notification role is missing; an admin can recreate it with `/stream add`."
        try:
            await channel.send(
                content,
                embed=build_live_embed(a.stream, a.info),
                allowed_mentions=discord.AllowedMentions(roles=[role] if role else False),
            )
        except discord.Forbidden:
            log.warning("Missing permission to post in channel %s of guild %s", a.channel_id, guild.id)
            return True
        log.info("Announced %s in guild %s", a.stream.login, guild.id)
        return True


def build_live_embed(stream: TrackedStream, info: LiveInfo) -> discord.Embed:
    embed = discord.Embed(
        title=info.title or "Untitled stream",
        url=stream.url,
        colour=TWITCH_PURPLE,
        timestamp=info.started_at,
    )
    embed.set_author(name=info.display_name, url=stream.url)
    if info.game_name:
        embed.add_field(name="Game", value=info.game_name, inline=True)
    embed.add_field(name="Viewers", value=str(info.viewer_count), inline=True)
    if info.thumbnail_url:
        # Cache-buster so Discord doesn't show a preview from an earlier stream.
        thumb = info.thumbnail_url.replace("{width}", "1280").replace("{height}", "720")
        embed.set_image(url=f"{thumb}?t={int(time.time())}")
    embed.set_footer(text="Live on Twitch")
    return embed


def setup_logging(config: Config) -> None:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(config.log_dir / "bot.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(config.log_level)
    root.handlers[:] = [file_handler, console]
    # discord.py is very chatty at DEBUG; keep it at INFO unless explicitly debugging.
    logging.getLogger("discord").setLevel(
        max(logging.INFO, root.level) if config.log_level != "DEBUG" else logging.DEBUG
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="Main.py", description="Twitch live notifications for Discord")
    parser.add_argument("--version", action="version", version=f"TwitchDiscordBot {__version__}")
    parser.add_argument(
        "--check", action="store_true", help="verify .env and the Discord/Twitch credentials, then exit"
    )
    args = parser.parse_args(argv)

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        from .check import print_results, run_checks

        print(f"Configuration file: {config.env_file}")
        return 0 if print_results(asyncio.run(run_checks(config))) else 1
    setup_logging(config)
    bot = TwitchNotifierBot(config)
    try:
        bot.run(config.discord_token, log_handler=None)
    except discord.LoginFailure:
        log.critical("Discord rejected the bot token; check DISCORD_TOKEN in %s", config.env_file)
        return 2
    except discord.PrivilegedIntentsRequired:
        log.critical(MEMBERS_INTENT_HELP)
        return 2
    return bot.exit_code
