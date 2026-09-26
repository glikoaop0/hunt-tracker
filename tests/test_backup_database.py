import sqlite3
from pathlib import Path

import pytest

from app.db import backup_database


def _make_db(path: Path, rows: int) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE hunt (id INTEGER PRIMARY KEY, title TEXT)")
        conn.executemany("INSERT INTO hunt (title) VALUES (?)", [(f"h{i}",) for i in range(rows)])
        conn.commit()
    finally:
        conn.close()


def _row_count(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM hunt").fetchone()[0]
    finally:
        conn.close()


def test_backup_creates_folder_and_copies_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "hunts.db"
    _make_db(db_path, rows=3)
    backup_path = tmp_path / "backups" / "hunts.backup.db"

    result = backup_database(db_path, backup_path)

    assert result == backup_path
    assert _row_count(backup_path) == 3


def test_backup_overwrites_the_single_backup_file(tmp_path: Path) -> None:
    db_path = tmp_path / "hunts.db"
    _make_db(db_path, rows=1)
    backup_path = tmp_path / "backups" / "hunts.backup.db"
    backup_database(db_path, backup_path)

    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO hunt (title) VALUES ('later')")
    conn.commit()
    conn.close()
    backup_database(db_path, backup_path)

    assert _row_count(backup_path) == 2
    assert sorted(p.name for p in backup_path.parent.iterdir()) == ["hunts.backup.db"]


def test_backup_includes_uncheckpointed_wal_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "hunts.db"
    _make_db(db_path, rows=0)
    writer = sqlite3.connect(db_path)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO hunt (title) VALUES ('in wal')")
        writer.commit()
        backup_path = backup_database(db_path, tmp_path / "backups" / "hunts.backup.db")
    finally:
        writer.close()

    assert _row_count(backup_path) == 1


def test_backup_leaves_live_database_unchanged(tmp_path: Path) -> None:
    db_path = tmp_path / "hunts.db"
    _make_db(db_path, rows=2)
    before = db_path.read_bytes()

    backup_database(db_path, tmp_path / "backups" / "hunts.backup.db")

    assert db_path.read_bytes() == before


def test_backup_fails_clearly_and_keeps_previous_backup_when_database_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "hunts.db"
    _make_db(db_path, rows=4)
    backup_path = tmp_path / "backups" / "hunts.backup.db"
    backup_database(db_path, backup_path)
    db_path.unlink()

    with pytest.raises(RuntimeError, match="No database found"):
        backup_database(db_path, backup_path)

    assert _row_count(backup_path) == 4
