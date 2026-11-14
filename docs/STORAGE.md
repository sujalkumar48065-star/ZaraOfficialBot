# Zara storage operations

Zara uses the `zara/db/` package for all persistence. Handlers keep writing the
SQLite-flavoured SQL they already used; the adapter translates it for TiDB/MySQL
and returns `sqlite3.Row`-compatible `Row` objects.

## Backends

| Backend | When to use | Selection |
| --- | --- | --- |
| SQLite | Local development, single instance, simple backups | Default; `ZARA_DB_BACKEND=sqlite` |
| MySQL/TiDB | Render/production, multiple workers, managed backups | `ZARA_DB_BACKEND=mysql` + `ZARA_DB_URL` |

SQLite uses WAL mode, `synchronous=FULL`, `BEGIN IMMEDIATE` writes, and a
30-second busy timeout. TiDB uses SQLAlchemy pooling, pre-ping, 240-second
connection recycling, TLS, and retries for transient read errors.

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `ZARA_DB_BACKEND` | `sqlite` | `sqlite` or `mysql` |
| `ZARA_DB_URL` | empty | Full `mysql://user:pass@host:4000/db?ssl=true` URL |
| `ZARA_DB_HOST` | empty | TiDB gateway host when `ZARA_DB_URL` is absent |
| `ZARA_DB_PORT` | `4000` | TiDB MySQL port |
| `ZARA_DB_USER` | empty | TiDB user, including its prefix |
| `ZARA_DB_PASSWORD` | empty | TiDB password |
| `ZARA_DB_NAME` | `zara` | Database name |
| `ZARA_DB_SSL` | `required` | `required`, `skip-verify`, or `disabled` |
| `ZARA_DB_SSL_CA` | empty | CA file path, inline PEM, or `base64:<data>` |
| `ZARA_DB_POOL_SIZE` | `5` | SQLAlchemy pool size |
| `ZARA_DB_MAX_OVERFLOW` | `5` | Extra pooled connections |
| `ZARA_DB_POOL_RECYCLE` | `240` | Recycle before TiDB's 340-second idle timeout |
| `ZARA_DB_POOL_TIMEOUT` | `30` | Seconds to wait for a pooled connection |
| `ZARA_DB_CONNECT_TIMEOUT` | `10` | TCP connect timeout |
| `ZARA_DB_READ_TIMEOUT` | `30` | Read timeout |
| `ZARA_DB_WRITE_TIMEOUT` | `30` | Write timeout |
| `ZARA_DB_RETRIES` | `3` | Retries for transient read failures |
| `ZARA_DB_RETRY_DELAY` | `0.25` | Base backoff delay |
| `ZARA_ENCRYPTION_KEY` | empty | Fernet key for sensitive columns and exports |
| `ZARA_BACKUP_DIR` | `backups` | Backup/export directory |
| `ZARA_LEGACY_DB_PATH` | `data/zara.sqlite3` | Source for one-time legacy migration |
| `ZARA_AUTO_MIGRATE_LEGACY` | `0` | Import legacy SQLite when the target has no users |

`ZARA_DB_URL` takes precedence over the discrete `ZARA_DB_*` fields. The parser
accepts `mysql://`, `mysql+pymysql://`, `tidb://`, and `sqlite://` URLs.

## Encryption

Sensitive columns are encrypted with Fernet when `ZARA_ENCRYPTION_KEY` is set:
`users.username`, `users.name`, chat titles/welcome/goodbye/rules, filter
responses, and game/hack/bomb session state. Ciphertext uses the `enc:v1:`
prefix. Usernames also get a keyed blind index in `users.username_hash` for
case-insensitive lookup.

Generate a key once:

```bash
python -m zara.db.cli init-key
```

Store it as a Render secret and keep an offline copy. Losing the key makes the
encrypted columns and encrypted exports unreadable. Never commit it.

To encrypt rows created before the key was enabled:

```bash
python -m zara.db.cli reencrypt --yes
```

If encrypted data exists but the key is missing, startup health checks fail with
`EncryptionKeyMissingError` instead of silently serving ciphertext.

## Schema

Schema version: **3**. The canonical tables are:

`users`, `chats`, `chat_members`, `game_sessions`, `hack_sessions`,
`bomb_sessions`, `blacklists`, `filters`, `action_counters`, `warnings`,
`couples`, `items`, `approvals`, `user_settings`, `storage_meta`,
`schema_migrations`.

`initialize()` creates missing tables, adds new columns with `ALTER TABLE`, adds
indexes, records migrations, and verifies the final schema. It is safe to run on
every startup.

## CLI

```bash
python -m zara.db.cli init
python -m zara.db.cli health
python -m zara.db.cli verify
python -m zara.db.cli backup
python -m zara.db.cli export --output /tmp/zara-export.json.enc
python -m zara.db.cli export --no-encrypt --output /tmp/zara-export.json
python -m zara.db.cli import /tmp/zara-export.json.enc --dry-run
python -m zara.db.cli import /tmp/zara-export.json.enc --yes
python -m zara.db.cli restore backups/zara-backup-....json.enc --yes
python -m zara.db.cli reencrypt --yes
python -m zara.db.cli delete-user --user-id 123456 --yes
python -m zara.db.cli purge-inactive --days 180
python -m zara.db.cli purge-inactive --days 180 --apply
```

`restore` writes a pre-restore backup before changing data and verifies every
restored row. Exports contain a checksum; corrupted or edited files are rejected.

## TiDB setup

1. Create a TiDB Cloud Serverless cluster and a database (for example `zara`).
2. Create a database user. TiDB Cloud shows a username with a prefix such as
   `user.root`; use the **full username** in `ZARA_DB_USER`.
3. Download the cluster CA certificate and mount it as a Render secret file,
   for example `/etc/secrets/tidb-ca.pem`.
4. Set `ZARA_DB_URL` or the discrete fields. Example:

```text
ZARA_DB_BACKEND=mysql
ZARA_DB_URL=mysql://user.prefix:password@gateway01.ap-northeast-1.prod.aws.tidbcloud.com:4000/zara?ssl=true
ZARA_DB_SSL=required
ZARA_DB_SSL_CA=/etc/secrets/tidb-ca.pem
ZARA_ENCRYPTION_KEY=<Fernet key>
```

5. Create the schema and verify:

```bash
python -m zara.db.cli init
python -m zara.db.cli verify
```

6. Import an existing SQLite database once:

```bash
ZARA_LEGACY_DB_PATH=/path/to/data/zara.sqlite3 \
python -m zara.db.cli migrate --yes
```

Migration copies the source SQLite file, backs up the target, imports every
table, fills `username_hash`, and verifies row counts before reporting success.

## Backups and retention

- Run `backup` on a schedule and copy the encrypted file off Render.
- Keep the encryption key separate from the backup.
- `purge-inactive` is a dry run unless `--apply` is passed; configured owner and
  admin IDs are excluded.
- `delete-user` removes the user, couples, items, settings, approvals, warnings,
  chat membership, and their entries in active game sessions.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `Temporary failure in name resolution` | TiDB host is wrong; use the exact host from the TiDB console |
| `Access denied` | Use the full TiDB username prefix and reset the password |
| TLS/certificate error | Set `ZARA_DB_SSL_CA` to the downloaded CA or use `skip-verify` temporarily |
| `EncryptionKeyMissingError` | Restore the original `ZARA_ENCRYPTION_KEY` |
| `Could not decrypt a stored value` | The key does not match the database |
| SQLite `database is locked` | Another process is writing; use one instance or TiDB |
| Schema incomplete | Run `python -m zara.db.cli init` and check the log |

## Tests

```bash
python -m pytest -q tests/test_storage.py
```

TiDB integration is opt-in and uses a dedicated test database:

```bash
ZARA_TEST_MYSQL_URL='mysql://user:pass@host:4000/zara_test?ssl=true' \
ZARA_DB_SSL_CA=/path/to/ca.pem \
python -m pytest -q tests/test_storage_mysql.py
```
