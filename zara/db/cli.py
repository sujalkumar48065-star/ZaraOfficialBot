"""Command line storage operations.

Examples:
    python -m zara.db.cli init-key
    python -m zara.db.cli health
    python -m zara.db.cli migrate --yes
    python -m zara.db.cli backup
    python -m zara.db.cli restore backups/zara-backup-....json.enc --yes
    python -m zara.db.cli export --output /tmp/zara-export.json.enc
    python -m zara.db.cli export --no-encrypt --output /tmp/zara-export.json
    python -m zara.db.cli import /tmp/zara-export.json.enc --yes
    python -m zara.db.cli reencrypt
    python -m zara.db.cli verify
    python -m zara.db.cli purge-inactive --days 180
    python -m zara.db.cli purge-inactive --days 180 --apply
    python -m zara.db.cli delete-user --user-id 123456 --yes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .config import DatabaseConfig
from .core import create_database
from .crypto import EncryptionCodec
from .errors import StorageError
from .maintenance import (
    backup,
    delete_user,
    export_payload,
    export_to_file,
    import_payload,
    integrity_report,
    migrate_legacy,
    purge_inactive,
    read_export,
    reencrypt_all,
    restore,
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _config(args: argparse.Namespace) -> DatabaseConfig:
    return DatabaseConfig.from_env()


def _db(args: argparse.Namespace):
    config = _config(args)
    database = create_database(config, initialize=True)
    return config, database


def _excluded_user_ids() -> set[int]:
    values: set[int] = set()
    for name in ("ZARA_OWNER_ID", "ZARA_ADMIN_IDS"):
        for item in (os.getenv(name, "") or "").split(","):
            item = item.strip()
            if item.isdigit():
                values.add(int(item))
    return values


def _require_yes(args: argparse.Namespace, action: str) -> None:
    if not getattr(args, "yes", False):
        raise StorageError(
            f"{action} writes to the configured database. Re-run with --yes to confirm."
        )


def command_init_key(args: argparse.Namespace) -> int:
    print(EncryptionCodec.generate_key())
    return 0


def command_health(args: argparse.Namespace) -> int:
    _, database = _db(args)
    try:
        health = database.health_check()
        report = integrity_report(database)
        print(_json({"health": health, "integrity": report}))
        return 0
    finally:
        database.close()


def command_init(args: argparse.Namespace) -> int:
    config, database = _db(args)
    try:
        print(_json({"initialized": True, "config": config.sanitized(), "health": database.health_check()}))
        return 0
    finally:
        database.close()


def command_migrate(args: argparse.Namespace) -> int:
    _require_yes(args, "Legacy migration")
    config, database = _db(args)
    try:
        result = migrate_legacy(
            Path(args.source) if args.source else config.legacy_sqlite_path,
            database,
            Path(args.backup_dir) if args.backup_dir else config.backup_dir,
        )
        print(_json({"migration": result, "health": database.health_check()}))
        return 0
    finally:
        database.close()


def command_backup(args: argparse.Namespace) -> int:
    config, database = _db(args)
    try:
        result = backup(
            database,
            Path(args.dir) if args.dir else config.backup_dir,
            encrypt=not args.no_encrypt,
        )
        print(_json(result))
        return 0
    finally:
        database.close()


def command_export(args: argparse.Namespace) -> int:
    config, database = _db(args)
    try:
        output = Path(args.output) if args.output else (
            (Path(args.dir) if args.dir else config.backup_dir)
            / f"zara-export-{int(__import__('time').time())}.json"
            + ("" if args.no_encrypt else ".enc")
        )
        result = export_to_file(
            database,
            output,
            encrypt=not args.no_encrypt,
            force=args.force,
        )
        print(_json(result))
        return 0
    finally:
        database.close()


def command_import(args: argparse.Namespace) -> int:
    config, database = _db(args)
    try:
        payload = read_export(Path(args.input), database.codec)
        if args.dry_run:
            print(
                _json(
                    {
                        "dry_run": True,
                        "schema_version": payload.get("schema_version"),
                        "row_counts": payload.get("row_counts"),
                        "checksum": payload.get("checksum"),
                        "tables": len(payload.get("tables", {})),
                    }
                )
            )
            return 0
        _require_yes(args, "Import")
        result = import_payload(database, payload, replace=args.replace)
        print(_json({"import": result, "health": database.health_check()}))
        return 0
    finally:
        database.close()


def command_restore(args: argparse.Namespace) -> int:
    _require_yes(args, "Restore")
    config, database = _db(args)
    try:
        result = restore(
            database,
            Path(args.input),
            replace=args.replace,
            backup_dir=Path(args.backup_dir) if args.backup_dir else config.backup_dir,
        )
        print(_json({"restore": result, "health": database.health_check()}))
        return 0
    finally:
        database.close()


def command_reencrypt(args: argparse.Namespace) -> int:
    _require_yes(args, "Re-encryption")
    _, database = _db(args)
    try:
        result = reencrypt_all(database)
        print(_json({"reencrypt": result, "health": database.health_check()}))
        return 0
    finally:
        database.close()


def command_verify(args: argparse.Namespace) -> int:
    _, database = _db(args)
    try:
        report = integrity_report(database)
        report["health"] = database.health_check()
        print(_json(report))
        return 0
    finally:
        database.close()


def command_delete_user(args: argparse.Namespace) -> int:
    _require_yes(args, "User deletion")
    _, database = _db(args)
    try:
        result = delete_user(database, int(args.user_id))
        print(_json({"deleted": result, "user_id": int(args.user_id)}))
        return 0
    finally:
        database.close()


def command_purge(args: argparse.Namespace) -> int:
    _, database = _db(args)
    try:
        result = purge_inactive(
            database,
            int(args.days),
            dry_run=not args.apply,
            exclude_ids=_excluded_user_ids(),
        )
        print(_json(result))
        return 0
    finally:
        database.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m zara.db.cli", description="Zara storage tools")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-key", help="generate a new Fernet key for ZARA_ENCRYPTION_KEY")
    sub.add_parser("health", help="probe the database and verify the schema")
    sub.add_parser("init", help="create/upgrade tables and print the resolved config")
    sub.add_parser("verify", help="print row counts, encryption and health")

    migrate = sub.add_parser("migrate", help="copy the legacy SQLite database into the target")
    migrate.add_argument("--source", help="legacy SQLite path (defaults to ZARA_LEGACY_DB_PATH)")
    migrate.add_argument("--backup-dir", help="where to store source/target backups")
    migrate.add_argument("--yes", action="store_true")

    backup_parser = sub.add_parser("backup", help="write an encrypted logical backup")
    backup_parser.add_argument("--dir", help="backup directory")
    backup_parser.add_argument("--no-encrypt", action="store_true")

    export = sub.add_parser("export", help="write a logical export")
    export.add_argument("--output", help="output file")
    export.add_argument("--dir", help="output directory when --output is omitted")
    export.add_argument("--no-encrypt", action="store_true")
    export.add_argument("--force", action="store_true")

    importer = sub.add_parser("import", help="import a logical export")
    importer.add_argument("input")
    importer.add_argument("--replace", action="store_true", help="delete existing rows first")
    importer.add_argument("--dry-run", action="store_true")
    importer.add_argument("--yes", action="store_true")

    restore_parser = sub.add_parser("restore", help="restore a backup/export")
    restore_parser.add_argument("input")
    restore_parser.add_argument("--replace", action="store_true")
    restore_parser.add_argument("--backup-dir", help="pre-restore backup directory")
    restore_parser.add_argument("--yes", action="store_true")

    reencrypt = sub.add_parser("reencrypt", help="encrypt existing plaintext sensitive columns")
    reencrypt.add_argument("--yes", action="store_true")

    delete = sub.add_parser("delete-user", help="delete one user and their related records")
    delete.add_argument("--user-id", required=True, type=int)
    delete.add_argument("--yes", action="store_true")

    purge = sub.add_parser("purge-inactive", help="list or delete users inactive for N days")
    purge.add_argument("--days", required=True, type=int)
    purge.add_argument("--apply", action="store_true", help="actually delete (default is dry-run)")
    return parser


COMMANDS = {
    "init-key": command_init_key,
    "health": command_health,
    "init": command_init,
    "verify": command_verify,
    "migrate": command_migrate,
    "backup": command_backup,
    "export": command_export,
    "import": command_import,
    "restore": command_restore,
    "reencrypt": command_reencrypt,
    "delete-user": command_delete_user,
    "purge-inactive": command_purge,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(COMMANDS[args.command](args))
    except StorageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
