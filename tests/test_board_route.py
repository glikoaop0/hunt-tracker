import re
from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models import Hunt, HuntStatus, Run, RunOutcome


def columns_by_status(html: str) -> dict[HuntStatus, str]:
    positions = [(status, html.index(f'id="column-{status.value}"')) for status in HuntStatus]
    columns = {}
    for i, (status, start) in enumerate(positions):
        end = positions[i + 1][1] if i + 1 < len(positions) else len(html)
        columns[status] = html[start:end]
    return columns


@pytest.fixture()
def client() -> Iterator[TestClient]:
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
        rundll32 = Hunt(
            title="Rundll32 abuse",
            hypothesis="Attackers abuse rundll32 for execution",
            status=HuntStatus.idea,
            priority=2,
            attack_techniques="T1218",
        )
        golden_ticket = Hunt(
            title="Golden ticket",
            hypothesis="Forged Kerberos tickets used for persistence",
            status=HuntStatus.active,
            priority=1,
            attack_techniques="T1558.001, T1558.002",
            cadence_days=14,
            next_run=date.today() - timedelta(days=3),
        )
        session.add_all([rundll32, golden_ticket])
        session.commit()
        session.refresh(rundll32)
        session.refresh(golden_ticket)

        session.add_all(
            [
                Run(hunt_id=golden_ticket.id, outcome=RunOutcome.no_findings, notes="Clean pass."),
                Run(hunt_id=golden_ticket.id, outcome=RunOutcome.findings, notes="Confirmed forged ticket."),
            ]
        )
        session.commit()

    yield TestClient(app)

    app.dependency_overrides.clear()


def test_board_returns_ok(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200


def test_board_has_four_status_columns(client: TestClient) -> None:
    response = client.get("/")
    for status in HuntStatus:
        assert f'data-status="{status.value}"' in response.text


def test_board_card_shows_title_priority_technique_and_run_count(client: TestClient) -> None:
    response = client.get("/")
    assert "Rundll32 abuse" in response.text
    assert "Priority 2" in response.text
    assert "1 technique" in response.text
    assert "0 runs" in response.text

    assert "Golden ticket" in response.text
    assert "Priority 1" in response.text
    assert "2 techniques" in response.text
    assert "2 runs" in response.text


def test_board_places_hunts_in_matching_columns(client: TestClient) -> None:
    response = client.get("/")
    columns = columns_by_status(response.text)

    assert "Rundll32 abuse" in columns[HuntStatus.idea]
    assert "Golden ticket" not in columns[HuntStatus.idea]
    assert "Golden ticket" in columns[HuntStatus.active]
    assert "Rundll32 abuse" not in columns[HuntStatus.active]


def test_board_card_shows_overdue_state(client: TestClient) -> None:
    response = client.get("/")
    columns = columns_by_status(response.text)

    assert "Overdue" in columns[HuntStatus.active]
    assert "Overdue" not in columns[HuntStatus.idea]


def test_move_hunt_status_moves_card_between_columns(client: TestClient) -> None:
    board_response = client.get("/")
    columns = columns_by_status(board_response.text)
    hunt_id = re.search(r'data-hunt-id="(\d+)"', columns[HuntStatus.idea]).group(1)

    response = client.post(f"/hunts/{hunt_id}/status", data={"status": "active"})
    assert response.status_code == 200

    columns = columns_by_status(response.text)
    assert "Rundll32 abuse" in columns[HuntStatus.active]
    assert "Rundll32 abuse" not in columns[HuntStatus.idea]


def test_move_hunt_status_returns_404_for_unknown_hunt(client: TestClient) -> None:
    response = client.post("/hunts/999/status", data={"status": "active"})
    assert response.status_code == 404


def test_move_hunt_status_rejects_invalid_status(client: TestClient) -> None:
    board_response = client.get("/")
    columns = columns_by_status(board_response.text)
    hunt_id = re.search(r'data-hunt-id="(\d+)"', columns[HuntStatus.idea]).group(1)

    response = client.post(f"/hunts/{hunt_id}/status", data={"status": "not-a-status"})
    assert response.status_code == 422
