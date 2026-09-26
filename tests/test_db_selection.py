import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import DB_MODE_ENV, resolve_db_mode
from app.main import app
from app.routes import templates

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_TABLES = {"hunt", "run", "exclusion", "campaign", "campaignhunt"}


def test_unset_or_empty_selects_private() -> None:
    assert resolve_db_mode(None) == "private"
    assert resolve_db_mode("") == "private"
    assert resolve_db_mode("private") == "private"


def test_demo_selects_demo() -> None:
    assert resolve_db_mode("demo") == "demo"
    assert resolve_db_mode(" DEMO ") == "demo"


def test_unknown_value_is_refused_not_defaulted_to_private() -> None:
    with pytest.raises(RuntimeError, match=DB_MODE_ENV):
        resolve_db_mode("dmeo")


def test_demo_badge_only_in_demo_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)

    monkeypatch.setitem(templates.env.globals, "demo_mode", False)
    assert "DEMO DATA" not in client.get("/campaigns/new").text

    monkeypatch.setitem(templates.env.globals, "demo_mode", True)
    assert "DEMO DATA" in client.get("/campaigns/new").text


# The subprocess tests run a copy of the app package inside tmp_path, so
# PROJECT_ROOT resolves there and the real hunts.db / backups/ are never reachable.


@pytest.fixture()
def sandbox(tmp_path: Path) -> Path:
    shutil.copytree(PROJECT_ROOT / "app", tmp_path / "app", ignore=shutil.ignore_patterns("__pycache__"))
    private = sqlite3.connect(tmp_path / "hunts.db")
    try:
        private.execute("CREATE TABLE marker (value TEXT)")
        private.execute("INSERT INTO marker VALUES ('private data')")
        private.commit()
    finally:
        private.close()
    return tmp_path


def _run(sandbox: Path, mode: str | None, code: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != DB_MODE_ENV}
    if mode is not None:
        env[DB_MODE_ENV] = mode
    return subprocess.run(
        [sys.executable, "-c", code], cwd=sandbox, env=env, capture_output=True, text=True, timeout=60, check=True
    )


def _fingerprint(path: Path) -> tuple[str, int]:
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


def _tables(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def test_module_selects_file_from_environment(sandbox: Path) -> None:
    code = "from app.db import DB_PATH, BACKUP_PATH, DATABASE_URL; print(DB_PATH.name, BACKUP_PATH.name, DATABASE_URL)"

    private = _run(sandbox, None, code).stdout.split()
    demo = _run(sandbox, "demo", code).stdout.split()

    assert private[:2] == ["hunts.db", "hunts.backup.db"]
    assert private[2].endswith("hunts.db")
    assert demo[:2] == ["demo.db", "demo.backup.db"]
    assert demo[2].endswith("demo.db")


def test_demo_startup_initializes_empty_demo_db_and_leaves_hunts_db_untouched(sandbox: Path) -> None:
    before = _fingerprint(sandbox / "hunts.db")

    result = _run(
        sandbox,
        "demo",
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n"
        "with TestClient(app) as c:\n"
        "    r = c.get('/')\n"
        "    assert r.status_code == 200, r.status_code\n"
        "    print('DEMO DATA' in r.text)\n",
    )

    assert result.stdout.strip() == "True"
    assert _fingerprint(sandbox / "hunts.db") == before
    assert _tables(sandbox / "hunts.db") == {"marker"}
    assert not (sandbox / "backups").exists()

    demo = sandbox / "demo.db"
    assert EXPECTED_TABLES <= _tables(demo)
    conn = sqlite3.connect(demo)
    try:
        for table in EXPECTED_TABLES:
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
    finally:
        conn.close()


def test_migrate_in_demo_mode_targets_demo_db_only(sandbox: Path) -> None:
    before = _fingerprint(sandbox / "hunts.db")

    _run(sandbox, "demo", "from fastapi.testclient import TestClient\nfrom app.main import app\nwith TestClient(app): pass\n")
    result = _run(sandbox, "demo", "from app.migrate import migrate; migrate()")

    assert "already up to date" in result.stdout
    assert _fingerprint(sandbox / "hunts.db") == before
    assert not (sandbox / "backups" / "hunts.backup.db").exists()
