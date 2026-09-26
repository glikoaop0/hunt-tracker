from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models import Hunt, HuntStatus, RetirementReason
from tests.test_board_route import columns_by_status


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
        hunt = Hunt(
            title="Credential dumping via LSASS access",
            hypothesis="Unusual LSASS handle access indicates credential theft tooling",
            status=HuntStatus.active,
            priority=1,
        )
        session.add(hunt)
        session.commit()
        session.refresh(hunt)
        hunt_id = hunt.id

    yield TestClient(app), hunt_id, engine

    app.dependency_overrides.clear()


def test_retire_route_requires_reason(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, _ = client
    response = test_client.post(f"/hunts/{hunt_id}/retire", data={"note": "No reason given."})
    assert response.status_code == 422


def test_retire_route_rejects_invalid_reason(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, _ = client
    response = test_client.post(f"/hunts/{hunt_id}/retire", data={"reason": "not_a_real_reason"})
    assert response.status_code == 422


def test_retire_route_accepts_blank_note(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, _ = client
    response = test_client.post(
        f"/hunts/{hunt_id}/retire", data={"reason": "no_longer_relevant"}
    )
    assert response.status_code == 200


def test_retire_route_sets_retired_at_and_moves_card_to_retired_column(
    client: tuple[TestClient, int, object],
) -> None:
    test_client, hunt_id, engine = client
    response = test_client.post(
        f"/hunts/{hunt_id}/retire",
        data={"reason": "converted_to_detection", "note": "Now a standing detection."},
    )
    assert response.status_code == 200

    columns = columns_by_status(response.text)
    assert "Credential dumping via LSASS access" in columns[HuntStatus.retired]

    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.status == HuntStatus.retired
        assert hunt.retirement_reason == RetirementReason.converted_to_detection
        assert hunt.retirement_note == "Now a standing detection."
        assert hunt.retired_at is not None


def test_retire_route_returns_404_for_unknown_hunt(client: tuple[TestClient, int, object]) -> None:
    test_client, _, _ = client
    response = test_client.post("/hunts/999/retire", data={"reason": "superseded"})
    assert response.status_code == 404


def test_retire_dialog_returns_reason_options_and_hunt_title(
    client: tuple[TestClient, int, object],
) -> None:
    test_client, hunt_id, _ = client
    response = test_client.get(f"/hunts/{hunt_id}/retire-dialog")

    assert response.status_code == 200
    assert "Credential dumping via LSASS access" in response.text
    for reason in RetirementReason:
        assert reason.label in response.text


def test_retire_dialog_returns_404_for_unknown_hunt(client: tuple[TestClient, int, object]) -> None:
    test_client, _, _ = client
    response = test_client.get("/hunts/999/retire-dialog")
    assert response.status_code == 404


def test_generic_status_route_rejects_retired(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    response = test_client.post(f"/hunts/{hunt_id}/status", data={"status": "retired"})

    assert response.status_code == 400
    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.status == HuntStatus.active
        assert hunt.retirement_reason is None


def test_generic_status_route_still_allows_idea_scoped_active(
    client: tuple[TestClient, int, object],
) -> None:
    test_client, hunt_id, _ = client
    response = test_client.post(f"/hunts/{hunt_id}/status", data={"status": "scoped"})

    assert response.status_code == 200
    columns = columns_by_status(response.text)
    assert "Credential dumping via LSASS access" in columns[HuntStatus.scoped]


def test_same_status_move_is_noop_via_route(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        original_updated_at = hunt.updated_at

    response = test_client.post(f"/hunts/{hunt_id}/status", data={"status": "active"})
    assert response.status_code == 200

    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.updated_at == original_updated_at


def test_reactivating_then_retiring_again_replaces_previous_reason(
    client: tuple[TestClient, int, object],
) -> None:
    test_client, hunt_id, engine = client

    test_client.post(
        f"/hunts/{hunt_id}/retire", data={"reason": "missing_telemetry", "note": "First cycle."}
    )
    test_client.post(f"/hunts/{hunt_id}/status", data={"status": "active"})
    test_client.post(
        f"/hunts/{hunt_id}/retire", data={"reason": "superseded", "note": "Second cycle."}
    )

    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.status == HuntStatus.retired
        assert hunt.retirement_reason == RetirementReason.superseded
        assert hunt.retirement_note == "Second cycle."
