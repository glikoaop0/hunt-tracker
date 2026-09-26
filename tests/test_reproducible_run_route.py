from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models import Hunt, HuntStatus, Run, RunOutcome


@pytest.fixture()
def client() -> Iterator[tuple[TestClient, int, object]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    with Session(engine) as session:
        hunt = Hunt(
            title="Golden ticket forgery",
            hypothesis="Forged Kerberos tickets used for persistence",
            query="index=win_events EventCode=4768\n| where ticket_lifetime > 36000",
            status=HuntStatus.active,
            priority=1,
        )
        session.add(hunt)
        session.commit()
        session.refresh(hunt)
        hunt_id = hunt.id

    yield TestClient(app), hunt_id, engine

    app.dependency_overrides.clear()


def test_run_logging_captures_query_snapshot(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    response = test_client.post(
        f"/hunts/{hunt_id}/runs",
        data={"outcome": "no_findings", "notes": "Clean pass.", "search_from": "", "search_to": "", "result_count": ""},
    )

    assert response.status_code == 200
    with Session(engine) as session:
        run = session.exec(select(Run).where(Run.hunt_id == hunt_id)).first()
        assert run.query_snapshot == "index=win_events EventCode=4768\n| where ticket_lifetime > 36000"


def test_browser_submitted_query_snapshot_is_ignored(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    response = test_client.post(
        f"/hunts/{hunt_id}/runs",
        data={
            "outcome": "no_findings",
            "notes": "Attempted override.",
            "query_snapshot": "index=malicious injected query",
        },
    )

    assert response.status_code == 200
    with Session(engine) as session:
        run = session.exec(select(Run).where(Run.hunt_id == hunt_id)).first()
        assert run.query_snapshot == "index=win_events EventCode=4768\n| where ticket_lifetime > 36000"
        assert "malicious" not in run.query_snapshot


def test_valid_search_window_and_result_count_persist(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    response = test_client.post(
        f"/hunts/{hunt_id}/runs",
        data={
            "outcome": "findings",
            "notes": "Found it.",
            "search_from": "2026-01-01T08:00",
            "search_to": "2026-01-01T20:00",
            "result_count": "42",
        },
    )

    assert response.status_code == 200
    with Session(engine) as session:
        run = session.exec(select(Run).where(Run.hunt_id == hunt_id)).first()
        assert run.search_from.isoformat() == "2026-01-01T08:00:00"
        assert run.search_to.isoformat() == "2026-01-01T20:00:00"
        assert run.result_count == 42


def test_reversed_search_window_is_rejected_and_preserves_values(
    client: tuple[TestClient, int, object],
) -> None:
    test_client, hunt_id, engine = client
    response = test_client.post(
        f"/hunts/{hunt_id}/runs",
        data={
            "outcome": "findings",
            "notes": "Should not save.",
            "search_from": "2026-01-02T00:00",
            "search_to": "2026-01-01T00:00",
            "result_count": "5",
        },
    )

    assert response.status_code == 422
    assert "Should not save." in response.text
    assert "2026-01-02T00:00" in response.text
    with Session(engine) as session:
        assert session.exec(select(Run).where(Run.hunt_id == hunt_id)).first() is None


def test_negative_result_count_is_rejected(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    response = test_client.post(
        f"/hunts/{hunt_id}/runs",
        data={"outcome": "no_findings", "result_count": "-1"},
    )

    assert response.status_code == 422
    with Session(engine) as session:
        assert session.exec(select(Run).where(Run.hunt_id == hunt_id)).first() is None


def test_zero_result_count_is_accepted(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    response = test_client.post(
        f"/hunts/{hunt_id}/runs",
        data={"outcome": "no_findings", "result_count": "0"},
    )

    assert response.status_code == 200
    with Session(engine) as session:
        run = session.exec(select(Run).where(Run.hunt_id == hunt_id)).first()
        assert run.result_count == 0


def test_incomplete_search_window_is_rejected(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    response = test_client.post(
        f"/hunts/{hunt_id}/runs",
        data={"outcome": "no_findings", "search_from": "2026-01-01T00:00", "search_to": ""},
    )

    assert response.status_code == 422
    with Session(engine) as session:
        assert session.exec(select(Run).where(Run.hunt_id == hunt_id)).first() is None


def test_invalid_datetime_text_is_rejected(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    response = test_client.post(
        f"/hunts/{hunt_id}/runs",
        data={"outcome": "no_findings", "search_from": "not-a-date", "search_to": "2026-01-01T00:00"},
    )

    assert response.status_code == 422
    with Session(engine) as session:
        assert session.exec(select(Run).where(Run.hunt_id == hunt_id)).first() is None


def test_expanded_row_shows_query_snapshot(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    test_client.post(f"/hunts/{hunt_id}/runs", data={"outcome": "no_findings"})

    with Session(engine) as session:
        run_id = session.exec(select(Run).where(Run.hunt_id == hunt_id)).first().id

    response = test_client.get(f"/hunts/{hunt_id}/runs/{run_id}/row", params={"expanded": "true"})

    assert response.status_code == 200
    assert "index=win_events EventCode=4768" in response.text
    assert "immutable" in response.text.lower()


def test_legacy_run_with_null_snapshot_renders_safely(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    with Session(engine) as session:
        legacy_run = Run(hunt_id=hunt_id, outcome=RunOutcome.no_findings, notes="Old run.")
        session.add(legacy_run)
        session.commit()
        session.refresh(legacy_run)
        run_id = legacy_run.id

    collapsed = test_client.get(f"/hunts/{hunt_id}")
    assert collapsed.status_code == 200
    assert "Old run." in collapsed.text

    expanded = test_client.get(f"/hunts/{hunt_id}/runs/{run_id}/row", params={"expanded": "true"})
    assert expanded.status_code == 200
    assert "No query captured" in expanded.text


def test_collapsed_row_shows_window_and_result_count(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, _ = client
    response = test_client.post(
        f"/hunts/{hunt_id}/runs",
        data={
            "outcome": "findings",
            "search_from": "2026-01-01T08:00",
            "search_to": "2026-01-01T20:00",
            "result_count": "7",
        },
    )

    assert response.status_code == 200
    assert "2026-01-01 08:00" in response.text
    assert "2026-01-01 20:00" in response.text
    assert "7 results" in response.text


def test_htmx_run_logging_still_targets_hunt_main(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, _ = client
    response = test_client.post(f"/hunts/{hunt_id}/runs", data={"outcome": "no_findings"})

    assert response.status_code == 200
    assert 'id="hunt-main"' in response.text
