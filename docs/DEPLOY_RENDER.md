# Render deployment notes

This document covers the bot's TiDB storage, the verification Mini App, and the
environment variables that must be set on Render. Never commit real tokens,
database passwords, or the Fernet key.

## Bot service

The bot uses Telegram long polling, so it does not need a public HTTP port. On
Render, use a **Background Worker** with:

```text
Build command: pip install -r requirements.txt
Start command: python bot.py
```

A Render Web Service is only appropriate if you add an HTTP health endpoint and
bind it to `$PORT`; the bot itself does not serve HTTP.

Set these environment variables on the worker:

```text
ZARA_BOT_TOKEN=<BotFather token>
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
ZARA_VERIFY_WEBAPP_URL=https://zara-verify-soft.onrender.com/
ZARA_SELF_PING_URL=https://zara-verify-soft.onrender.com/
ZARA_SELF_PING_INTERVAL=240
ZARA_SELF_PING_DELAY=30
```

Add the TiDB variables from [STORAGE.md](STORAGE.md), including
`ZARA_ENCRYPTION_KEY`. The bot runs `initialize()` and a health check at startup;
a bad database or missing encryption key stops startup with a clear log line.

## TiDB Cloud

1. Create a Serverless cluster and a `zara` database.
2. Create a user and copy the full username (including the prefix).
3. Allow connections from Render. For a quick setup, allow all egress IPs; for
   production, restrict to Render's static/egress addresses if available.
4. Download the CA certificate and add it as a Render secret file at
   `/etc/secrets/tidb-ca.pem`.
5. Set `ZARA_DB_BACKEND=mysql`, `ZARA_DB_URL`, `ZARA_DB_SSL=required`, and
   `ZARA_DB_SSL_CA=/etc/secrets/tidb-ca.pem`.

Run the one-time schema and migration commands from a Render shell or a one-off
job using the same environment:

```bash
python -m zara.db.cli init
python -m zara.db.cli migrate --yes
python -m zara.db.cli verify
```

Do not run the legacy migration twice. `migrate` is additive and verifies row
counts, but it is intended as a one-time cutover. Keep the SQLite file and the
generated backups until the TiDB database has been verified.

## Verification Mini App

The Mini App is a static HTML file at `webapp/index.html`. It is deployed as the
Render web service `zara-verify-mini-app`:

```text
URL: https://zara-verify-mini-app.onrender.com/
Dashboard: https://dashboard.render.com/web/srv-dbr7lkoae00c73eri0lg
```

Current Render setup:

- Image: `docker.io/library/python:3.12-alpine`
- Plan: free, region: Singapore
- Secret file: `index.html` (mounted at `/etc/secrets/index.html`)
- Command (quote-free; Render passes quotes literally):

```text
python3 -m http.server 10000 --directory /etc/secrets --bind 0.0.0.0
```

The bot keeps the free service awake with `ZARA_SELF_PING_URL` every 240 seconds.
Render free services can still spin down; the first request after sleep may be
slower.

After changing the Mini App, update the Render secret file and redeploy. Verify:

```bash
curl -fsS https://zara-verify-mini-app.onrender.com/ | head
```

The Mini App posts `{action:"verify", init_data, client_time}` through
`Telegram.WebApp.sendData`. The bot validates the HMAC-SHA256 signature, checks
that the signed user ID matches the sender, and then calls `getChatMember`.

## Required Telegram setup

The bot must be an **administrator** in the configured updates channel
(`ZARA_UPDATE_CHAT_ID`, currently `-1004395250570`). Without that permission,
`getChatMember` returns `Bad Request: member list is inaccessible`, so `/verify`
and the Mini App cannot confirm normal users. The bot only needs permission to
view members; it does not need to post there.

## Rollback

1. Keep the last known-good SQLite file and encrypted backups.
2. To return to SQLite, unset `ZARA_DB_BACKEND`/`ZARA_DB_URL` and set
   `ZARA_DB_PATH` to the SQLite file.
3. Restore a backup with `python -m zara.db.cli restore <file> --yes`.
4. Restart the bot and run `python -m zara.db.cli verify`.

## Health checks

```bash
python -m zara.db.cli health
python -m zara.db.cli verify
```

`health` checks connectivity, schema, encryption, and table count. `verify`
prints row counts, key fingerprint, and missing username indexes. The bot logs
`Storage healthy: backend=... schema=3` at startup.
