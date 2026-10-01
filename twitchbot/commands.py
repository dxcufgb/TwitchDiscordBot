"""Slash commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from .notify import split_message, strip_discord_invites
from .storage import TrackedStream, normalize_login

if TYPE_CHECKING:
    from .bot import TwitchNotifierBot

log = logging.getLogger(__name__)

MANAGE = discord.Permissions(manage_guild=True)
MAX_FILTERS = 25
MAX_FILTER_LEN = 100


def _bot(interaction: discord.Interaction) -> TwitchNotifierBot:
    return interaction.client  # type: ignore[return-value]


async def _reply(interaction: discord.Interaction, content: str, *, ephemeral: bool = True, **kwargs) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=ephemeral, **kwargs)
    else:
        await interaction.response.send_message(content, ephemeral=ephemeral, **kwargs)


async def stream_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if interaction.guild_id is None:
        return []
    guild = _bot(interaction).store.guilds.get(interaction.guild_id)
    if guild is None:
        return []
    current = current.lower()
    return [
        app_commands.Choice(name=s.name, value=login) for login, s in sorted(guild.streams.items()) if current in login
    ][:25]


def _find_stream(interaction: discord.Interaction, raw: str) -> TrackedStream | None:
    login = normalize_login(raw)
    guild = _bot(interaction).store.guilds.get(interaction.guild_id or 0)
    if login is None or guild is None:
        return None
    return guild.streams.get(login)


def _missing_channel_permissions(channel: discord.abc.GuildChannel, me: discord.Member) -> list[str]:
    perms = channel.permissions_for(me)
    needed = {
        "View Channel": perms.view_channel,
        "Send Messages": perms.send_messages,
        "Embed Links": perms.embed_links,
        "Mention @everyone, @here and All Roles": perms.mention_everyone,
        "Manage Roles": me.guild_permissions.manage_roles,
    }
    return [name for name, ok in needed.items() if not ok]


# ---- /setup ------------------------------------------------------------------
setup_group = app_commands.Group(
    name="setup", description="Configure the bot for this server", guild_only=True, default_permissions=MANAGE
)


@setup_group.command(name="channel", description="Choose the channel where live alerts are posted")
@app_commands.describe(channel="Alert channel (defaults to the current channel)")
async def setup_channel(interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
    bot = _bot(interaction)
    target = channel or interaction.channel
    if not isinstance(target, discord.TextChannel) or interaction.guild is None:
        await _reply(interaction, "Please pick a text channel.")
        return
    bot.store.guild(interaction.guild.id).channel_id = target.id
    bot.store.save()
    message = f"Live alerts will be posted in {target.mention}."
    missing = _missing_channel_permissions(target, interaction.guild.me)
    if missing:
        message += "\n⚠️ I'm missing these permissions there: " + ", ".join(missing)
    await _reply(interaction, message)


@setup_group.command(name="show", description="Show the current alert channel")
async def setup_show(interaction: discord.Interaction) -> None:
    guild = _bot(interaction).store.guilds.get(interaction.guild_id or 0)
    if guild is None or guild.channel_id is None:
        await _reply(interaction, "No alert channel is set. Use `/setup channel`.")
    else:
        await _reply(interaction, f"Live alerts are posted in <#{guild.channel_id}>.")


@setup_group.command(name="clear", description="Stop posting live alerts (streams and filters are kept)")
async def setup_clear(interaction: discord.Interaction) -> None:
    bot = _bot(interaction)
    bot.store.guild(interaction.guild_id or 0).channel_id = None
    bot.store.save()
    await _reply(interaction, "Alert channel cleared. Use `/setup channel` to choose a new one.")


# ---- /stream -----------------------------------------------------------------
stream_group = app_commands.Group(
    name="stream", description="Manage tracked Twitch streams", guild_only=True, default_permissions=MANAGE
)


@stream_group.command(name="add", description="Track a Twitch channel and create its notification role")
@app_commands.describe(channel="Twitch login or channel URL, e.g. dxcufgb")
async def stream_add(interaction: discord.Interaction, channel: str) -> None:
    bot = _bot(interaction)
    guild = interaction.guild
    login = normalize_login(channel)
    if guild is None or login is None:
        await _reply(interaction, f"`{channel}` isn't a valid Twitch channel name.")
        return
    await interaction.response.defer(thinking=True)

    user = await bot.fetch_twitch_user(login)
    if user is None:
        await _reply(interaction, f"Twitch channel `{login}` could not be found.", ephemeral=False)
        return

    state = bot.store.guild(guild.id)
    stream = state.streams.get(user.login)
    created = stream is None
    if stream is None:
        stream = TrackedStream(login=user.login)
        state.streams[user.login] = stream
    stream.user_id = user.id
    stream.display_name = user.display_name

    lines = [f"Now tracking **{user.display_name}** (<https://www.twitch.tv/{user.login}>)."]
    if not created:
        lines[0] = f"**{user.display_name}** is already tracked."
    try:
        role = await bot.ensure_role(guild, stream)
        lines.append(f"Members can get alerts with `/notify subscribe {user.login}` (role {role.mention}).")
        if isinstance(interaction.user, discord.Member) and role not in interaction.user.roles:
            await interaction.user.add_roles(role, reason="Added stream via /stream add")
    except discord.Forbidden:
        lines.append("⚠️ I need the **Manage Roles** permission to create/assign the notification role.")
    bot.store.save()

    if state.channel_id is None:
        lines.append("ℹ️ No alert channel set yet; use `/setup channel`.")
    description = strip_discord_invites(user.description)
    if description:
        lines.append(f">>> {description[:500]}")
    await _reply(interaction, "\n".join(lines), ephemeral=False, allowed_mentions=discord.AllowedMentions.none())


@stream_group.command(name="remove", description="Stop tracking a Twitch channel")
@app_commands.describe(channel="Tracked channel", delete_role="Also delete its notification role")
@app_commands.autocomplete(channel=stream_autocomplete)
async def stream_remove(interaction: discord.Interaction, channel: str, delete_role: bool = False) -> None:
    bot = _bot(interaction)
    stream = _find_stream(interaction, channel)
    if stream is None or interaction.guild is None:
        await _reply(interaction, f"`{channel}` isn't tracked here. See `/stream list`.")
        return
    del bot.store.guild(interaction.guild.id).streams[stream.login]
    message = f"Stopped tracking **{stream.name}**."
    if delete_role:
        role = bot.resolve_role(interaction.guild, stream)
        if role is not None:
            try:
                await role.delete(reason="Stream removed via /stream remove")
                message += " Role deleted."
            except discord.HTTPException:
                message += " ⚠️ I couldn't delete its role."
    bot.store.save()
    await _reply(interaction, message)


@stream_group.command(name="list", description="List the Twitch channels tracked in this server")
async def stream_list(interaction: discord.Interaction) -> None:
    await _send_stream_list(interaction, show_filters=True)


# ---- /filter -----------------------------------------------------------------
filter_group = app_commands.Group(
    name="filter",
    description="Only alert when a stream title contains certain text",
    guild_only=True,
    default_permissions=MANAGE,
)


@filter_group.command(name="add", description="Only alert when the title contains this text (case-insensitive)")
@app_commands.describe(channel="Tracked channel", text="Text the stream title must contain")
@app_commands.autocomplete(channel=stream_autocomplete)
async def filter_add(
    interaction: discord.Interaction, channel: str, text: app_commands.Range[str, 1, MAX_FILTER_LEN]
) -> None:
    stream = _find_stream(interaction, channel)
    text = text.strip()
    if stream is None:
        await _reply(interaction, f"`{channel}` isn't tracked here. See `/stream list`.")
        return
    if not text:
        await _reply(interaction, "The filter text can't be empty.")
        return
    if text.casefold() not in (f.casefold() for f in stream.filters):
        if len(stream.filters) >= MAX_FILTERS:
            await _reply(interaction, f"A stream can have at most {MAX_FILTERS} filters.")
            return
        stream.filters.append(text)
        _bot(interaction).store.save()
    shown = ", ".join(f'"{f}"' for f in stream.filters)
    await _reply(interaction, f"**{stream.name}** alerts only when the title contains any of: {shown}")


@filter_group.command(name="clear", description="Remove all title filters from a stream")
@app_commands.autocomplete(channel=stream_autocomplete)
async def filter_clear(interaction: discord.Interaction, channel: str) -> None:
    stream = _find_stream(interaction, channel)
    if stream is None:
        await _reply(interaction, f"`{channel}` isn't tracked here. See `/stream list`.")
        return
    stream.filters.clear()
    _bot(interaction).store.save()
    await _reply(interaction, f"Cleared filters; every **{stream.name}** stream will be announced.")


@filter_group.command(name="list", description="List active title filters")
async def filter_list(interaction: discord.Interaction) -> None:
    guild = _bot(interaction).store.guilds.get(interaction.guild_id or 0)
    lines = [
        f"**{s.name}**: " + ", ".join(f'"{f}"' for f in s.filters)
        for s in (guild.streams.values() if guild else [])
        if s.filters
    ]
    if not lines:
        await _reply(interaction, "No filters are active.")
        return
    for chunk in split_message(lines):
        await _reply(interaction, chunk)


# ---- /notify (everyone) -----------------------------------------------------
notify_group = app_commands.Group(name="notify", description="Choose which streams ping you", guild_only=True)


async def _toggle_role(interaction: discord.Interaction, channel: str, subscribe: bool) -> None:
    bot = _bot(interaction)
    stream = _find_stream(interaction, channel)
    member = interaction.user
    if stream is None or interaction.guild is None or not isinstance(member, discord.Member):
        await _reply(interaction, f"`{channel}` isn't tracked here. See `/notify list`.")
        return
    role = bot.resolve_role(interaction.guild, stream)
    if role is None:
        await _reply(
            interaction, "This stream's notification role is missing; ask an admin to run `/stream add` again."
        )
        return
    try:
        if subscribe:
            await member.add_roles(role, reason="/notify subscribe")
            await _reply(interaction, f"You'll be pinged when **{stream.name}** goes live.")
        else:
            await member.remove_roles(role, reason="/notify unsubscribe")
            await _reply(interaction, f"You won't be pinged for **{stream.name}** anymore.")
    except discord.Forbidden:
        await _reply(interaction, "I'm not allowed to change that role (missing **Manage Roles** or role order).")


@notify_group.command(name="subscribe", description="Get pinged when a stream goes live")
@app_commands.autocomplete(channel=stream_autocomplete)
async def notify_subscribe(interaction: discord.Interaction, channel: str) -> None:
    await _toggle_role(interaction, channel, subscribe=True)


@notify_group.command(name="unsubscribe", description="Stop getting pinged for a stream")
@app_commands.autocomplete(channel=stream_autocomplete)
async def notify_unsubscribe(interaction: discord.Interaction, channel: str) -> None:
    await _toggle_role(interaction, channel, subscribe=False)


@notify_group.command(name="list", description="List streams you can subscribe to")
async def notify_list(interaction: discord.Interaction) -> None:
    await _send_stream_list(interaction, show_filters=False)


async def _send_stream_list(interaction: discord.Interaction, *, show_filters: bool) -> None:
    bot = _bot(interaction)
    guild = bot.store.guilds.get(interaction.guild_id or 0)
    if guild is None or not guild.streams or interaction.guild is None:
        await _reply(interaction, "No streams are tracked in this server yet.")
        return
    member_roles = {r.id for r in getattr(interaction.user, "roles", [])}
    lines = []
    for stream in sorted(guild.streams.values(), key=lambda s: s.login):
        role = bot.resolve_role(interaction.guild, stream)
        mark = "🔔" if role is not None and role.id in member_roles else "🔕"
        line = f"{mark} **{stream.name}** <{stream.url}>"
        if show_filters and stream.filters:
            line += " (filters: " + ", ".join(f'"{f}"' for f in stream.filters) + ")"
        lines.append(line)
    for chunk in split_message(lines):
        await _reply(interaction, chunk)


# ---- misc --------------------------------------------------------------------
HELP_TEXT = """\
**Everyone**
`/notify list` – streams tracked here (🔔 = you get pinged)
`/notify subscribe <channel>` / `/notify unsubscribe <channel>` – toggle pings

**Managers** (Manage Server by default; change in Server Settings → Integrations)
`/setup channel [channel]` – where live alerts are posted
`/setup show` · `/setup clear`
`/stream add <channel>` – track a Twitch channel (creates a `TW-<name>-Role` role)
`/stream remove <channel> [delete_role]` · `/stream list`
`/filter add <channel> <text>` – only alert when the title contains the text
`/filter clear <channel>` · `/filter list`
"""


@app_commands.command(name="help", description="Show what the bot can do")
async def help_command(interaction: discord.Interaction) -> None:
    await _reply(interaction, HELP_TEXT)


@app_commands.command(name="restart", description="Restart the bot (bot owner only)")
@app_commands.default_permissions(administrator=True)
async def restart_command(interaction: discord.Interaction) -> None:
    bot = _bot(interaction)
    if not await bot.is_owner(interaction.user):
        await _reply(interaction, "Only the bot owner can do that.")
        return
    log.warning("Restart requested by %s (%s)", interaction.user, interaction.user.id)
    await _reply(interaction, "Restarting… (the service manager or `--watch` will start me again)")
    bot.exit_code = 0
    await bot.close()


async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    log.error("Command %s failed", interaction.command and interaction.command.qualified_name, exc_info=error)
    try:
        await _reply(interaction, "Something went wrong running that command. The error has been logged.")
    except discord.HTTPException:
        pass


def register_commands(bot: TwitchNotifierBot) -> None:
    for command in (setup_group, stream_group, filter_group, notify_group, help_command, restart_command):
        bot.tree.add_command(command)
    bot.tree.error(on_app_command_error)
