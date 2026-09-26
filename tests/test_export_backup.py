import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

import app.export as export_module
from app.db import get_session
from app.main import app
from app.models import Hunt, HuntStatus


@pytest.fixture()
def real_sqlite_db(tmp_path, monkeypatch) -> Iterator[Path]:
    """Point app.export at a real, on-disk SQLite file (not the shared in-memory
    test engine) so create_sqlite_backup() has an actual file to snapshot."""
    db_path = tmp_path / "hunts_test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(Hunt(title="Backup me", hypothesis="Testing backup", status=HuntStatus.idea))
        session.commit()

    monkeypatch.setattr(export_module, "DB_PATH", db_path)
    monkeypatch.setattr(export_module, "DATABASE_URL", f"sqlite:///{db_path}")

    yield db_path


def test_backup_is_a_readable_sqlite_database(real_sqlite_db: Path) -> None:
    backup_path = export_module.create_sqlite_backup()
    try:
        conn = sqlite3.connect(backup_path)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "hunt" in tables
    finally:
        backup_path.unlink(missing_ok=True)


def test_backup_contains_expected_rows(real_sqlite_db: Path) -> None:
    backup_path = export_module.create_sqlite_backup()
    try:
        conn = sqlite3.connect(backup_path)
        rows = conn.execute("SELECT title FROM hunt").fetchall()
        conn.close()
        assert rows == [("Backup me",)]
    finally:
        backup_path.unlink(missing_ok=True)


def test_live_database_file_is_unchanged_after_backup(real_sqlite_db: Path) -> None:
    original_bytes = real_sqlite_db.read_bytes()

    backup_path = export_module.create_sqlite_backup()
    backup_path.unlink(missing_ok=True)

    assert real_sqlite_db.read_bytes() == original_bytes


def test_temporary_backup_file_does_not_leak(real_sqlite_db: Path) -> None:
    backup_path = export_module.create_sqlite_backup()
    assert backup_path.exists()
    backup_path.unlink()
    assert not backup_path.exists()


def test_backup_fails_clearly_when_database_missing(tmp_path, monkeypatch) -> None:
    missing_path = tmp_path / "does_not_exist.db"
    monkeypatch.setattr(export_module, "DB_PATH", missing_path)
    monkeypatch.setattr(export_module, "DATABASE_URL", f"sqlite:///{missing_path}")

    with pytest.raises(RuntimeError):
        export_module.create_sqlite_backup()


def test_backup_fails_clearly_for_non_sqlite_configuration(monkeypatch) -> None:
    monkeypatch.setattr(export_module, "DATABASE_URL", "postgresql://example/db")

    with pytest.raises(RuntimeError):
        export_module.create_sqlite_backup()


def test_backup_route_ignores_path_like_query_params(real_sqlite_db: Path) -> None:
    # The route takes no parameters at all; a path-like query string is simply
    # ignored, since there is no mechanism to point the backup at an arbitrary file.
    test_client = TestClient(app)
    response = test_client.get("/export/backup", params={"path": "/etc/passwd"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.sqlite3"
