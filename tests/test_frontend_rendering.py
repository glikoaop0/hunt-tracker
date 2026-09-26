from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models import Hunt, HuntStatus


@pytest.fixture()
def client() -> Iterator[tuple[TestClient, int, int]]:
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
        overdue_hunt = Hunt(
            title="Overdue hunt",
            hypothesis="Should be flagged overdue",
            status=HuntStatus.active,
            priority=1,
            cadence_days=7,
            next_run=date.today() - timedelta(days=3),
        )
        annotated_hunt = Hunt(
            title="Annotated hunt",
            hypothesis="Has notes to display",
            status=HuntStatus.active,
            priority=3,
            notes="**Important** context for this hunt.",
        )
        bare_hunt = Hunt(
            title="Bare hunt",
            hypothesis="Nothing filled in beyond the required fields",
            status=HuntStatus.idea,
            priority=3,
        )
        session.add_all([overdue_hunt, annotated_hunt, bare_hunt])
        session.commit()
        for hunt in (overdue_hunt, annotated_hunt, bare_hunt):
            session.refresh(hunt)
        ids = (overdue_hunt.id, annotated_hunt.id, bare_hunt.id)

    yield TestClient(app), *ids

    app.dependency_overrides.clear()


def test_board_summary_strip_reflects_actual_counts(client: tuple[TestClient, int, int, int]) -> None:
    test_client, *_ = client
    response = test_client.get("/")

    assert response.status_code == 200
    assert "Total hunts" in response.text
    assert "Overdue" in response.text
    assert "Retired" in response.text
    assert 'class="summary-value">3<' in response.text  # total hunts


def test_board_overdue_card_shows_overdue_badge(client: tuple[TestClient, int, int, int]) -> None:
    test_client, overdue_id, _, _ = client
    response = test_client.get("/")

    assert "Overdue hunt" in response.text
    assert "Overdue" in response.text


def test_new_hunt_button_is_prominent_on_board(client: tuple[TestClient, int, int, int]) -> None:
    test_client, *_ = client
    response = test_client.get("/")

    assert 'class="btn btn-primary board-new-hunt"' in response.text


def test_hunt_detail_displays_notes(client: tuple[TestClient, int, int, int]) -> None:
    test_client, _, annotated_id, _ = client
    response = test_client.get(f"/hunts/{annotated_id}")

    assert response.status_code == 200
    assert "<h2>Notes</h2>" in response.text
    assert "<strong>Important</strong>" in response.text


def test_hunt_detail_omits_notes_section_when_empty(client: tuple[TestClient, int, int, int]) -> None:
    test_client, _, _, bare_id = client
    response = test_client.get(f"/hunts/{bare_id}")

    assert "<h2>Notes</h2>" not in response.text


def test_hunt_detail_shows_edit_action_as_visible_button(
    client: tuple[TestClient, int, int, int],
) -> None:
    test_client, _, _, bare_id = client
    response = test_client.get(f"/hunts/{bare_id}")

    assert f'href="/hunts/{bare_id}/edit"' in response.text
    assert "detail-edit-link" in response.text
    assert ">Edit hunt<" in response.text


def test_hunt_detail_shows_empty_query_state(client: tuple[TestClient, int, int, int]) -> None:
    test_client, _, _, bare_id = client
    response = test_client.get(f"/hunts/{bare_id}")

    assert "No query recorded yet." in response.text


def test_hunt_detail_shows_empty_run_history_state(client: tuple[TestClient, int, int, int]) -> None:
    test_client, _, _, bare_id = client
    response = test_client.get(f"/hunts/{bare_id}")

    assert "No runs logged yet." in response.text


def test_hunt_detail_shows_empty_exclusions_state(client: tuple[TestClient, int, int, int]) -> None:
    test_client, _, _, bare_id = client
    response = test_client.get(f"/hunts/{bare_id}")

    assert "No active exclusions." in response.text


def test_hunt_detail_sidebar_shows_summary_fields(client: tuple[TestClient, int, int, int]) -> None:
    test_client, overdue_id, _, _ = client
    response = test_client.get(f"/hunts/{overdue_id}")

    assert "<h2>Summary</h2>" in response.text
    assert "Cadence" in response.text
    assert "7 days" in response.text
    assert "Last run" in response.text


def test_board_and_detail_pages_share_the_shell_bar(client: tuple[TestClient, int, int, int]) -> None:
    test_client, _, annotated_id, _ = client
    board_response = test_client.get("/")
    detail_response = test_client.get(f"/hunts/{annotated_id}")

    assert 'class="shell-wordmark"' in board_response.text
    assert 'class="shell-wordmark"' in detail_response.text
    assert 'id="app-error"' in board_response.text
    assert 'id="app-error"' in detail_response.text
