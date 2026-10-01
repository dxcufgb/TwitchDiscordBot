# TwitchDiscordBot – Code Audit

Scope: `Main.py` (the whole bot) at commit `10753bf`. Line numbers refer to that commit.
Severity: **Critical** = data loss, security hole or bot stops working; **High** = feature is broken;
**Medium** = robustness/maintainability; **Low** = polish.

## 1. Summary

The bot works in the common case, but several bugs make it **lose data on Linux**, **stop
notifying every server** after one bad event, and **skip notifications** for streams just added.
There are also two permission holes. Most issues come from three design choices:

1. All logic lives in one `on_message` handler wrapped in a bare `try/except`.
2. State is spread over `.env`, `Streams.json` and `<guild>-Stream.json`, and the bot rewrites `.env` at runtime.
3. The polling loop has no error handling, so one exception ends it for good.

## 2. Critical

| # | Where | Problem | Fix |
|---|---|---|---|
| C1 | L22 vs L159/176/326 | Loads `streams.json` but writes `Streams.json`. Windows filenames are case-insensitive, Linux ones are not, so **on Linux every restart forgets all streams**. | Use one constant, e.g. `STREAMS_FILE = 'Streams.json'`. |
| C2 | L310-330, L338, L343-346 | `checkForDailyUpdates` has no error handling. Any exception ends the loop **for all servers** until restart, for example: a renamed or banned Twitch user (`data[0]` → `IndexError`), a server with streams but no `!sbc` channel (`int(None.strip())`), a deleted channel (`None.send`), a Twitch API timeout, or the `except` branch failing in turn. | Use `try/except Exception: logger.exception(...)` per stream and per server, inside a `discord.ext.tasks.loop`. |
| C3 | L62-68 | `updateEnv` joins entries with `''` and relies on old values still ending in `\n`. A channel ID set by `!sbc` has no newline, so after a second server runs `!sbc` the file reads `STREAMS-CHANNEL-1=123STREAMS-CHANNEL-2=456`, **which corrupts `.env`**. The rewrite also drops any `.env` key the bot doesn't know and isn't atomic, so a crash mid-write can wipe the **token**. | Never write to `.env`. Keep it read-only for secrets and store channel IDs in a JSON or SQLite file. |
| C4 | L123 | `!restart-bot` checks `display_name == 'dxcufgb'`. **Any member can set their nickname to `dxcufgb`** and restart the bot over and over (DoS). | Compare `message.author.id` with an owner ID from config, or use `(await self.application_info()).owner.id`. |
| C5 | L251-259 | `!remove-role` has no `TW-` check, so **any user can remove any role from themselves**, including moderation roles like "Muted", "Quarantine" or "Unverified". | Apply the same `TW-` check as `!add-role`, and better still only allow roles listed in `availableStreams`. |

## 3. High

| # | Where | Problem | Fix |
|---|---|---|---|
| H1 | L137, L163, L170 | When a new stream is added, the **same `innerValues` dict** goes into both `guildStreams` and `availableStreams`. The poller sets the shared `LastStatus` to `live`, so the "was not live before" check never passes and **the server that added the stream gets no notifications until the bot restarts**. (Confirmed by running it.) | Use separate dicts: `guildStreams[g][s] = {'LastStatus': ''}`. |
| H2 | L38/L54/L97 | `on_guild_join` and `!sbc` set `guildStreams[guild.id] = ''` (a string). The next `!add-stream` then fails with `TypeError: 'str' object does not support item assignment`, so **a server that invites the bot can't add streams until a restart**. | Use `{}` and create the server file at that point. |
| H3 | L141, L152 | Stream names aren't normalised and the duplicate check compares URLs case-sensitively, so `!add-stream Foo` and `!add-stream foo` create **two entries, two roles and two notifications** for the same channel. Surrounding whitespace also ends up in the key and the URL. | Normalise names (`.strip().lower()`) before looking them up and saving them. |
| H4 | L172 | The role is only created in the **first** server that adds a stream. Other servers get "role missing" and the fallback **pings `@here`** (L346), which can ping hundreds of people. | Create the role per server if missing (`discord.utils.get` → `create_role`), and never fall back to `@here`. |
| H5 | L12, L353 | `intents` is defined but never passed to the client. The code only runs on **discord.py 1.7.x** (now pinned in `requirements.txt`). On 2.x, `Client()` without intents fails, and reading `message.content` needs the privileged *Message Content* intent. | Move to discord.py 2.x with slash commands (see §6). |
| H6 | L71-74 | `twitchAPI` 2.x is **synchronous** (`requests`) and called inside `async` code, so every poll blocks the Discord event loop. A **new `Twitch()` (a new OAuth token) is also created for every stream every 60 s**. With many streams this delays heartbeats, causing disconnects, and can hit Twitch rate limits. | Create one `Twitch` client at start-up. Batch up to 100 logins in a single `get_streams(user_login=[...])` call. Use twitchAPI 4.x (async), or at least `asyncio.to_thread`. |
| H7 | L49 | `on_ready` runs again after every reconnect, and each run starts **another** polling loop that never ends. After a few reconnects you get duplicate API calls and possible duplicate notifications. | Start the loop once (`setup_hook` in 2.x, or a guard flag), or use `tasks.loop`, which won't start twice. |
| H8 | L184-185 | `re.sub(pattern, '', text, re.DOTALL)` passes `re.DOTALL` (16) as **`count`**, not `flags`. Because of the leading `.*`, the regex also deletes everything before an invite link. | `re.sub(pattern, '', text, flags=re.DOTALL)` with a simpler pattern such as `r'(https?://)?(www\.)?(discord\.gg|discord(app)?\.com/invite)/\S+'`. |
| H9 | L306-308 | The bare `except:` hides every error (nothing is logged), so bugs can't be diagnosed. If no channel is set, `int(None)` raises **inside** the handler. | Use `except Exception: logger.exception(...)` and reply in `message.channel`. |

## 4. Medium

| # | Where | Problem / suggestion |
|---|---|---|
| M1 | L16-20 | Hand-written `.env` parser: values containing `=` are cut off, lines without `=` or blank lines crash, `#` comments aren't supported, and CRLF/`\n` stay in the values (hence the `.strip()` calls). Use `python-dotenv`. |
| M2 | all commands | Everyone can add, remove and filter streams, which affects the shared state in every server. Limit `!add-stream`, `!remove-stream` and the filter commands to a moderator role or *Manage Server* permission. |
| M3 | L138, L190, L207, L221 | Arguments are parsed by fixed slicing (`content[12:]`), so `!add-stream  foo` or `!add-stream --name foo` (the help text tells users to type that) adds a stream literally named `--name foo`. Twitch logins aren't validated (`^[a-zA-Z0-9_]{4,25}$`). `startswith` also matches `!add-streamer`. Use `discord.ext.commands` or slash commands. |
| M4 | L191 | With no space, `find(' ')` returns `-1` and the last character of the stream name is silently dropped. |
| M5 | L201-203 | `filterText = ...` should be `+=`, so only the last filter is shown. Filters are also case-sensitive; consider `.casefold()`. |
| M6 | L212-236 | Empty lists call `channel.send('')`, which raises an error, so "Something went wrong" is shown. Lists over 2000 characters also fail. Show "No streams" and split long messages. |
| M7 | L229, L235 | The list commands raise `KeyError` if a stream has no `Role` (old data). Use `.get('Role', '-')`. |
| M8 | L220-224 | `!remove-stream` only removes the stream from the server. It stays in `availableStreams` and is **polled forever**. Remove it from `availableStreams` once no server uses it. The same applies when the bot leaves a server (`on_guild_remove`). |
| M9 | L59, L159, L326 | JSON files are written with plain `open('w+')`, which isn't atomic: a crash or power loss mid-write leaves an empty or corrupt file and the bot then fails to start. Write to `file.tmp` and then `os.replace()`. `Streams.json` is also rewritten for **every** stream on every poll, and the server files for every offline stream. Only write when something changes. |
| M10 | L5-10 | Root DEBUG logging floods the console with gateway traffic. `discord.log` uses `mode='w'`, so **each restart erases the log that explains the crash**. Use INFO, a `RotatingFileHandler` and `mode='a'`. |
| M11 | all | All paths are relative to the working directory, so starting the bot from another folder makes it create new empty state files. Resolve paths from `Path(__file__).parent`. The new start scripts handle this with `cd`. |
| M12 | L126 | `os.execv(sys.executable, ['python'] + sys.argv)` breaks on Windows paths with spaces and skips a clean shutdown. A cleaner approach is `await self.close()` and `sys.exit(…)`, letting the service manager restart the bot (now provided). |
| M13 | L79 | Uses the global `client.user` inside the class. Use `self.user`. |
| M14 | L27 | `__inti__` is a typo. It does nothing and can be removed. |
| M15 | L341 | Roles are created non-mentionable. Mentions only work while the bot has the *Mention @everyone* permission. Create the role with `mentionable=True`, or pass `allowed_mentions=AllowedMentions(roles=[role])`. |
| M16 | L172-175 | `create_role` and `add_roles` fail without *Manage Roles*, or if the bot's role is lower than the new role. Catch `discord.Forbidden` and tell the admin what permission is missing. |

## 5. Low / Polish

* No `requirements.txt` (added), no README setup steps (added), and no tests.
* `!available-streams` shows every server's streams to every other server.
* Use embeds for "went live" messages, with the stream thumbnail, game and title.
* Notify when a stream goes offline (optional) and edit the live message instead of posting a new one.
* Help text says "Created by Dxcufgb - 2022"; move it into an embed or `/about`.
* PEP 8 naming (`Stream`/`URL` local variables, camelCase methods).
* `.gitignore` ignores `*.json` and `*.bat`. Fine for data files, but no committed config or `.bat` file can exist. The Windows starter is therefore `start-bot.cmd`.

## 6. Recommended refactor (in priority order)

1. **Quick fixes** (each a small, safe change): C1, C4, C5, H1, H2, H8, M4, M5, M6, plus `try/except` with logging around each stream and server in the poller (C2).
2. **Single state store.** Move channel IDs out of `.env` into `data/state.json` (written atomically) or SQLite. Keep `.env` read-only for `TOKEN`, `TWITCH_APP_ID`, `TWITCH_APP_SECRET`, `OWNER_ID`. This fixes C3 and M9.
3. **Poller rewrite.** Use `@tasks.loop(seconds=60)`, one `Twitch` client, and batched `get_streams` calls. Track live state as a `set` of `user_id`s (IDs survive renames, logins don't), and compare previous with current to detect "went live". This fixes C2, H6, H7 and M8.
4. **discord.py 2.x + slash commands** (`/stream add`, `/stream remove`, `/filter add`, `/notify subscribe` with autocomplete). This removes the need for the privileged Message Content intent and the string parsing (M3, M4), and gives per-command permissions (`default_permissions(manage_guild=True)`).
5. **Optional: Twitch EventSub** (`stream.online` over WebSocket) instead of polling, for instant notifications and no rate-limit concerns.
6. **Packaging and ops.** Pinned `requirements.txt`, the start scripts and service installers (added), plus an optional `Dockerfile` and GitHub Actions running `ruff` and a few unit tests for the parsing and state code.
