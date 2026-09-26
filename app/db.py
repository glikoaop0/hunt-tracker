import os
import sqlite3
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DB_MODE_ENV = "HUNT_TRACKER_DB"
DB_FILES = {"private": "hunts.db", "demo": "demo.db"}
# Demo mode gets its own backup file so backing up demo.db can never overwrite
# the only backup of the real data.
BACKUP_FILES = {"private": "hunts.backup.db", "demo": "demo.backup.db"}


def resolve_db_mode(value: str | None) -> str:
    mode = (value or "private").strip().lower()
    if mode not in DB_FILES:
        # Refuse rather than fall back: a typo while asking for demo mode must
        # not silently open the private database.
        raise RuntimeError(f"{DB_MODE_ENV}={value!r} is not valid; use one of {sorted(DB_FILES)}.")
    return mode


DB_MODE = resolve_db_mode(os.environ.get(DB_MODE_ENV))
DEMO_MODE = DB_MODE == "demo"
DB_PATH = PROJECT_ROOT / DB_FILES[DB_MODE]
DATABASE_URL = f"sqlite:///{DB_PATH}"
BACKUP_PATH = PROJECT_ROOT / "backups" / BACKUP_FILES[DB_MODE]

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session


def backup_database(db_path: Path = DB_PATH, backup_path: Path = BACKUP_PATH) -> Path:
    if not db_path.exists():
        raise RuntimeError(f"No database found at {db_path}; nothing to back up.")

    backup_path.parent.mkdir(parents=True, exist_ok=True)
  
    tmp_path = backup_path.with_name(backup_path.name + ".tmp")
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dest = sqlite3.connect(tmp_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    os.replace(tmp_path, backup_path)
    return backup_path
