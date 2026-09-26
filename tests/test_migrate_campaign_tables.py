import sqlite3
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select

import app.migrate as migrate_module
from app.models import Campaign, CampaignHunt, Hunt, HuntStatus


def _create_pre_campaign_schema(db_path: Path) -> None:
    """Mimics the schema as it existed before this feature: hunt/run/exclusion
    tables, but the run table is also missing the reproducible-run columns,
    and there is no campaign or campaignhunt table at all."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE hunt (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                hypothesis TEXT NOT NULL,
                query TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'idea',
                priority INTEGER NOT NULL DEFAULT 3,
                data_sources TEXT NOT NULL DEFAULT '',
                attack_techniques TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                cadence_days INTEGER,
                next_run DATE,
                retirement_reason TEXT,
                retirement_note TEXT,
                retired_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE run (
                id INTEGER PRIMARY KEY,
                hunt_id INTEGER NOT NULL,
                ran_at DATETIME NOT NULL,
                outcome TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                duration_minutes INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE exclusion (
                id INTEGER PRIMARY KEY,
                hunt_id INTEGER NOT NULL,
                run_id INTEGER,
                value TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at DATETIME NOT NULL,
                active BOOLEAN NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            "INSERT INTO hunt (id, title, hypothesis, status, priority, created_at, updated_at) "
            "VALUES (1, 'Pre-existing hunt', 'It was here first', 'active', 2, "
            "'2026-01-01T00:00:00', '2026-01-01T00:00:00')"
        )
        conn.execute(
            "INSERT INTO run (id, hunt_id, ran_at, outcome, notes) "
            "VALUES (1, 1, '2026-01-02T00:00:00', 'no_findings', 'Ran clean')"
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def old_schema_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "old_hunts.db"
    _create_pre_campaign_schema(db_path)

    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(migrate_module, "DB_PATH", db_path)
    monkeypatch.setattr(migrate_module, "engine", test_engine)
    monkeypatch.setattr(migrate_module, "BACKUP_PATH", tmp_path / "backups" / "hunts.backup.db")
    return db_path


def test_migrate_adds_missing_run_columns(old_schema_db: Path) -> None:
    migrate_module.migrate()

    conn = sqlite3.connect(old_schema_db)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(run)")}
    finally:
        conn.close()

    for column in ("query_snapshot", "search_from", "search_to", "result_count"):
        assert column in columns


def test_migrate_creates_campaign_tables(old_schema_db: Path) -> None:
    migrate_module.migrate()

    conn = sqlite3.connect(old_schema_db)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()

    assert "campaign" in tables
    assert "campaignhunt" in tables


def test_migrate_preserves_existing_hunts_and_runs(old_schema_db: Path) -> None:
    migrate_module.migrate()

    with Session(migrate_module.engine) as session:
        hunts = session.exec(select(Hunt)).all()
        assert len(hunts) == 1
        assert hunts[0].title == "Pre-existing hunt"
        assert hunts[0].status == HuntStatus.active

        runs = session.exec(select(Hunt).where(Hunt.id == 1)).first().runs
        assert len(runs) == 1
        assert runs[0].notes == "Ran clean"


def test_migrate_new_tables_are_immediately_usable(old_schema_db: Path) -> None:
    migrate_module.migrate()

    with Session(migrate_module.engine) as session:
        campaign = Campaign(title="New campaign after migration", objective="Should just work")
        session.add(campaign)
        session.commit()
        session.refresh(campaign)

        hunt = session.exec(select(Hunt)).first()
        session.add(CampaignHunt(campaign_id=campaign.id, hunt_id=hunt.id))
        session.commit()

        reloaded = session.exec(select(Campaign)).first()
        assert len(reloaded.memberships) == 1


def test_migrate_is_idempotent(old_schema_db: Path, capsys: pytest.CaptureFixture) -> None:
    migrate_module.migrate()
    migrate_module.migrate()

    captured = capsys.readouterr()
    assert "Schema already up to date; nothing to do." in captured.out

    with Session(migrate_module.engine) as session:
        hunts = session.exec(select(Hunt)).all()
        assert len(hunts) == 1  # still exactly one -- no duplication from re-running


def test_migrate_backs_up_database_before_changing_schema(old_schema_db: Path, tmp_path: Path) -> None:
    migrate_module.migrate()

    backup_path = tmp_path / "backups" / "hunts.backup.db"
    assert backup_path.exists()
    assert list(tmp_path.glob("*.backup-*.db")) == []

    # the backup should reflect the OLD schema (no campaign table yet)
    conn = sqlite3.connect(backup_path)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()
    assert "campaign" not in tables
