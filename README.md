# TwitchDiscordBot

[![Latest release](https://img.shields.io/github/v/release/dxcufgb/TwitchDiscordBot?label=release&color=ff6400)](https://github.com/dxcufgb/TwitchDiscordBot/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/dxcufgb/TwitchDiscordBot/total?label=downloads&color=blue)](https://github.com/dxcufgb/TwitchDiscordBot/releases)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-support-FF5E5B?logo=kofi&logoColor=white)](https://ko-fi.com/dxcufgb)\
[![CI](https://img.shields.io/github/actions/workflow/status/dxcufgb/TwitchDiscordBot/ci.yml?branch=main&label=CI)](https://github.com/dxcufgb/TwitchDiscordBot/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/dxcufgb/TwitchDiscordBot)](LICENSE)

A Discord bot that posts an alert, and pings a per-stream role, when the Twitch channels you track go live.

Built on **discord.py 2.7** (slash commands) and **twitchAPI 4.5** (async Helix API). Requires Python 3.10 or newer.

## Installation

You need a Discord application and a Twitch application first: see [Discord and Twitch Setup](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Discord-and-Twitch-Setup).

Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/dxcufgb/install/main/install.sh | sudo bash -s twitchdiscordbot
```

Windows: run `twitchdiscordbot-setup.exe` from the [latest release](../../releases/latest).

Installer options, scripted installs and running from source: see [Installation](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Installation) in the wiki.

## Documentation

The full guide is in the [wiki](https://github.com/dxcufgb/TwitchDiscordBot/wiki):

- [Discord and Twitch Setup](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Discord-and-Twitch-Setup): the two applications, the invite and the credentials.
- [Installation](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Installation): Linux and Windows installers, their options and what they do.
- [Commands](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Commands): every slash command and who may use it.
- [Automatic Updates](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Automatic-Updates): the opt-in daily updater.
- [Update Notifications](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Update-Notifications): the DM server admins get after an update.
- [Files and Configuration](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Files-and-Configuration): `.env`, the data files, and running from a source checkout.
- [Upgrading from 1.x](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Upgrading-from-1.x): what the first start imports.
- [Troubleshooting](https://github.com/dxcufgb/TwitchDiscordBot/wiki/Troubleshooting): missing or old slash commands.

## License

[MIT](LICENSE)
