# Zara Telegram Bot

Zara is a persistent Telegram gaming, economy, moderation, inline and Hinglish AI bot.

## Project structure

The bot is organized as a package so each area can be understood without
searching one giant file:

```text
ZaraOfficialBot/
├── bot.py                    # launcher, callbacks, and handler wiring
├── zara/
│   ├── config.py             # environment settings and sticker-pack links
│   ├── command_registry.py   # one checked command registry
│   ├── menus.py              # start, games, and card keyboards
│   ├── tools.py              # key-free tool APIs and media helpers
│   ├── db/                   # SQLite/TiDB storage, encryption, backups, CLI
│   └── handlers/
│       ├── core.py           # start, help, identity, AI
│       ├── economy.py        # balance, items, relationships, premium
│       ├── games.py          # cards, bomb, roulette, prompts
│       ├── actions.py        # GIF reactions
│       ├── stickers.py       # Telegram sticker commands
│       ├── groups.py         # rules, filters, approvals, warnings
│       ├── utilities.py      # custom emoji utility
│       ├── tools.py          # weather, media, math and lookup commands
│       ├── admin.py          # administrator moderation commands
│       └── owner.py          # owner-only `/m...` controls
├── webapp/index.html         # Telegram verification Mini App
├── tests/                    # storage tests
└── docs/                     # storage and Render/TiDB deployment notes
```

Every feature module exports a command family. The registry rejects duplicate
names or missing handler functions during startup, so adding a command is
visible and auditable instead of silently overriding another command.

The active storage layer is `zara/db/`. See [docs/STORAGE.md](docs/STORAGE.md)
for encryption, backups, legacy migration, and TiDB setup.

## Run

Set these as secure environment secrets (never commit them):

- `ZARA_BOT_TOKEN` — required
- `XAI_API_KEY` — preferred Grok provider for normal chat
- `XAI_MODEL` — Grok model, defaults to `grok-4`
- `OPENROUTER_API_KEY` — fallback AI provider
- `OPENAI_API_KEY` — optional fallback when OpenRouter is unavailable
- `GIPHY_API_KEY` — optional secure GIPHY fallback key for PG-13 anime GIFs

Normal chat is command-free: Zara replies to every DM text message, and in
groups only when replied to, mentioned, or addressed by name. Stickers get a
sticker reply in DM or when the sticker is a reply to Zara.

Then run from the project root:

```bash
python bot.py
```

Storage tests:

```bash
python -m pytest -q tests/test_storage.py
```

Optional environment variables:

```text
ZARA_OWNER_ID=8799679469
ZARA_ADMIN_IDS=8799679469
ZARA_UPDATE_CHANNEL=
ZARA_UPDATE_CHANNEL_URL=https://t.me/+HjHouGnrhjNlZTA1
ZARA_UPDATE_CHAT_ID=-1004395250570
ZARA_UPDATE_CHANNEL_LABEL=Zara Updates
ZARA_SUPPORT_USERNAME=Tilx1
ZARA_DEVELOPER_USERNAME=Tilx1
ZARA_PAYMENT_URL=
ZARA_POLICY_URL=
XAI_MODEL=grok-4
XAI_BASE_URL=https://api.x.ai/v1
OPENROUTER_MODEL=openai/gpt-4o-mini
ZARA_AI_NAME_TRIGGERS=zara,zarra,zaara,mee,zarabot,meezara,meezarabot
ZARA_DB_PATH=data/zara.sqlite3
ZARA_REMOTE_GIFS=0
ZARA_VERIFY_WEBAPP_URL=
ZARA_SELF_PING_URL=
```

The bot stores users, balances, gems, premium status, protection, chat settings, filters, and card-game lobbies in SQLite by default. Set `ZARA_DB_BACKEND=mysql` and `ZARA_DB_URL` to use TiDB/MySQL in production. Secrets are intentionally not part of this archive.

## Tools

The Tools category in Explore contains key-free everyday commands:

```text
/weather Delhi          current weather and 3-day forecast
/time Delhi             local time and timezone
/define hello           English meaning and examples
/wiki India             Wikipedia summary
/tts hi kaise ho        text-to-speech MP3 audio
/qr https://t.me/x      QR code image
/image cute cat         AI-generated image
/format png             convert a replied image to png, jpg or webp
/calc 2+2*5             safe calculator
/shorten https://...    short link
/translate hi hello     translate text
/currency 100 usd inr   currency conversion
/poll Best game? | Card | Bomb | Roulette
```

`/tts` sends an MP3 audio file because the server does not include ffmpeg for
voice-note conversion. `/format` works when replying to a photo or image
document. `/image` uses the free Pollinations image endpoint.

## GIF actions

GIF actions use the public Nekos.best v2 anime GIF API first, so `/kiss`, `/hug`, `/slap`, `/punch`, `/murder`, `/bite`, `/love`, `/crush`, `/pat`, `/cuddle`, `/dance`, `/highfive`, and `/bonk` get action-specific anime animations. Bundled GIFs remain the final fallback, so the commands still work without any external API. Reply to a user's message or pass `@username`/Telegram ID:

```text
/kiss @username
/hug 123456789
```

Remote generic GIF fallback is disabled. Zara uses Nekos.best first, then
anime-only GIPHY searches when the secure key is available, then bundled anime
animations.

## GIPHY and stickers

Zara searches Nekos.best for action-specific anime GIFs without an API key. If
Nekos.best is unavailable and `GIPHY_API_KEY` is configured as a secure Replit
Secret, Zara falls back to PG-13 anime GIPHY searches. If both are unavailable,
the bundled anime animations are sent automatically.

The configured Telegram sticker packs are loaded at startup from Telegram and are not copied into the ZIP. Telegram file IDs are reused safely at runtime:

```text
/sticker
/sticker funny_cute_cats
/stickerpack
```

`/stickerpack` lists the packs that Telegram made available to the bot and their sticker counts. If a public pack was renamed, deleted, or inaccessible, Zara reports it in the startup log and continues with the other packs.

## Start menu and profile photo

`/start` opens an English profile overview showing balance, rank, gems, and
kills. Its buttons are Explore, Updates, Support, Add Zara in your group, and
Developer.

Use `/pfp` or reply to a user's message with `/pfp` to show their latest
Telegram profile photo. Zara also replies when someone mentions “zara” in a
normal message.

## GIF commands and inline mode

Action commands are target-specific and work in groups with Telegram's bot
suffix as well:

```text
/hug @username
/hug 123456789
/hug@Meee_Zarabot @username
```

Replying to a user's message is the most reliable target format:

```text
Reply to a message with /kiss
Reply to a message with /love
Reply to a message with /punch
```

If an explicit username is not known to Zara, the bot asks for a reply or
Telegram ID instead of accidentally sending the action to the sender.

Inline mode provides anime-only action-specific Nekos.best GIF choices, with GIPHY as fallback. In any Telegram chat type
`@Meee_Zarabot hug`, `@Meee_Zarabot kiss`, `@Meee_Zarabot love`,
`@Meee_Zarabot slap`, or another action name, then choose a result. The
empty inline menu also contains quick buttons for common actions.

## Automatic Zara chat and stickers

Zara responds to ordinary text messages without requiring `/ai`. For
example, send `Hey! Kaise ho?` and Zara can reply naturally in Hinglish.
The `/ai your message` command remains supported as an optional explicit form.
In groups, automatic chat and sticker replies happen only when the message
is a direct reply to one of Zara's messages; otherwise Zara stays quiet.
Messages such as `sticker please`, `haha`, or `😂` can trigger a random sticker
without a command; a short cooldown prevents sticker spam.

`/verify` includes a Telegram Mini App button. Join the updates channel, open
the verifier, and tap **Verify membership**. The bot checks membership again
server-side before confirming the user.

Each GIF action fetches a small Nekos.best pool, caches it briefly, and avoids
repeating recent URLs for that action. If Nekos.best is unavailable, the GIPHY
and bundled anime GIF fallbacks remain available.

Owner and admin accounts are visible in `/leaderboard`, `/topkill`, and
`/leaders`; staff resources display as `∞`, while owner/admin protection rules
remain unchanged.

Action and relationship messages use current clickable Telegram mentions
instead of stale stored display names. `/love` and `/crush` use a stable,
pair-specific percentage, so changing the selected users changes the value.

## Card game

1. Add Zara to a group and give it the needed permissions.
2. `/bet 2000` starts a lobby.
3. Other players use `/card`.
4. Zara attempts to DM each player's five cards. Players choose with `/flip a`, `/flip b`, `/flip c`, `/flip d`, or `/flip e` in the group.
5. Five rounds are scored and the pot is split among the winners.

Telegram only allows a bot to DM users who have opened the bot at least once, so players should press Start in Zara before playing.

## Safety and permissions

- Owner: `8799679469` (override with `ZARA_OWNER_ID`)
- Admin: `8799679469` (override with comma-separated `ZARA_ADMIN_IDS`)
- Owner/admin resources are virtual unlimited and are not included in public leaderboards.
- Owner/admin users cannot be robbed or killed.
- `/close` and `/open` require a group admin or configured staff user.
- Normal users must join the updates channel and pass `/verify` before claiming the fixed 2,000-coin `/daily` reward; premium, owner, and admin users bypass this check.
- Payment links are informational/manual; no payment is marked successful automatically.
- The bot does not force users to join the updates channel; `/daily` checks membership when a normal user claims the reward.
- Owner-only commands begin with `/m` and are omitted from normal users' command menu.
- Admin moderation commands are shown only in the configured admin's private command menu.

## Notes

The bundled image at `assets/start.jpg` is used automatically as the `/start` image in private chats and groups. Set `ZARA_START_IMAGE` to use another image.

Revoke any tokens that were pasted in chat and use fresh values in secure secrets before production use.

## ZIP contents

- `bot.py` — compatible launcher
- `zara/` — configuration, storage, menus, and command-family modules
- `assets/start.jpg` — bundled start image
- `assets/gifs/` — bundled animated action GIFs
- `.env.example` — configuration template without secrets
- `requirements.txt` — Python dependencies
- `data/` — created automatically for persistent SQLite data