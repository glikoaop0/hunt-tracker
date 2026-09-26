"""Additive, idempotent schema migration for the Hunt Tracker database.

There is no migration framework in this project (deliberately, to keep the
project dependency-light) and SQLModel.metadata.create_all() only creates missing tables,
never adds columns to an existing one. This script is the documented, manual
alternative for the cases that need it: adding new nullable columns to an
existing table (ALTER TABLE, hand-rolled), and creating whole new tables
(delegated to SQLModel.metadata.create_all(), which is itself additive-only
and safe to call against a database that already has other tables/data).

Run manually:
    python -m app.migrate

Safe to run repeatedly: it inspects the existing schema first and only adds
what's actually missing. Never invoked automatically by the app.
"""

import sqlite3

from sqlmodel import SQLModel

from app import models  
from app.db import BACKUP_PATH, DB_PATH, backup_database, engine

NEW_RUN_COLUMNS = {
    "query_snapshot": "TEXT",
    "search_from": "DATETIME",
    "search_to": "DATETIME",
    "result_count": "INTEGER",
}

NEW_TABLES = {"campaign", "campaignhunt"}


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row[0] for row in rows}


def _backup(reason: str) -> None:
    backup_path = backup_database(DB_PATH, BACKUP_PATH)
    print(f"Backup written to {backup_path} (reason: {reason})")


def migrate() -> None:
    if not DB_PATH.exists():
        print(f"No database found at {DB_PATH}; nothing to migrate.")
        print("Start the app once to create a fresh, empty database with the current schema.")
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        existing_run_columns = _existing_columns(conn, "run")
        missing_run_columns = {
            name: sqltype for name, sqltype in NEW_RUN_COLUMNS.items() if name not in existing_run_columns
        }
        missing_tables = NEW_TABLES - _existing_tables(conn)

        if not missing_run_columns and not missing_tables:
            print("Schema already up to date; nothing to do.")
            return

        conn.close()
        _backup(reason="adding run columns and/or campaign tables")

        if missing_run_columns:
            conn = sqlite3.connect(DB_PATH)
            try:
                for name, sqltype in missing_run_columns.items():
                    conn.execute(f"ALTER TABLE run ADD COLUMN {name} {sqltype}")
                    print(f"Added column run.{name} ({sqltype})")
                conn.commit()
            finally:
                conn.close()

        if missing_tables:
            SQLModel.metadata.create_all(engine)
            verify_conn = sqlite3.connect(DB_PATH)
            try:
                still_missing = missing_tables - _existing_tables(verify_conn)
            finally:
                verify_conn.close()
            if still_missing:
                raise RuntimeError(
                    f"create_all() did not create expected table(s): {sorted(still_missing)}. "
                    "Check that every SQLModel table class is imported before this runs."
                )
            for table in sorted(missing_tables):
                print(f"Created table {table}")

        print("Migration complete.")
    finally:
        conn.close()  

if __name__ == "__main__":
    migrate()
