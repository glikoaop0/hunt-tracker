from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models import Hunt, HuntStatus


@pytest.fixture()
def client() -> Iterator[tuple[TestClient, object]]:
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

    yield TestClient(app), engine

    app.dependency_overrides.clear()


def test_new_hunt_form_renders_successfully(client: tuple[TestClient, object]) -> None:
    test_client, _ = client
    response = test_client.get("/hunts/new")

    assert response.status_code == 200
    assert "<form" in response.text
    assert 'name="title"' in response.text
    assert 'name="hypothesis"' in response.text
    assert 'name="priority"' in response.text


def test_new_hunt_form_shows_lifecycle_guide(client: tuple[TestClient, object]) -> None:
    test_client, _ = client
    response = test_client.get("/hunts/new")

    assert "From idea to recurring hunt" in response.text
    assert "Drag the card to Retired" in response.text


def test_new_hunt_action_appears_once_on_board(client: tuple[TestClient, object]) -> None:
    test_client, _ = client
    response = test_client.get("/")

    assert response.status_code == 200
    assert response.text.count('href="/hunts/new"') == 1

    board_start = response.text.index('id="board"')
    idea_start = response.text.index('id="column-idea"')
    new_hunt_index = response.text.index('href="/hunts/new"')
    assert new_hunt_index < board_start < idea_start


def test_valid_creation_persists_a_hunt(client: tuple[TestClient, object]) -> None:
    test_client, engine = client
    test_client.post(
        "/hunts",
        data={"title": "Rundll32 abuse", "hypothesis": "Attackers abuse rundll32.", "priority": "2"},
        follow_redirects=False,
    )

    with Session(engine) as session:
        hunts = session.exec(select(Hunt)).all()
        assert len(hunts) == 1
        assert hunts[0].title == "Rundll32 abuse"


def test_valid_creation_returns_303_redirect_to_detail_page(
    client: tuple[TestClient, object],
) -> None:
    test_client, engine = client
    response = test_client.post(
        "/hunts",
        data={"title": "Rundll32 abuse", "hypothesis": "Attackers abuse rundll32.", "priority": "2"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(engine) as session:
        hunt = session.exec(select(Hunt)).first()
        assert response.headers["location"] == f"/hunts/{hunt.id}"


def test_created_hunt_starts_in_idea(client: tuple[TestClient, object]) -> None:
    test_client, engine = client
    test_client.post(
        "/hunts",
        data={"title": "Rundll32 abuse", "hypothesis": "Attackers abuse rundll32.", "priority": "2"},
        follow_redirects=False,
    )

    with Session(engine) as session:
        hunt = session.exec(select(Hunt)).first()
        assert hunt.status == HuntStatus.idea


def test_created_hunt_keeps_optional_fields_at_model_defaults(
    client: tuple[TestClient, object],
) -> None:
    test_client, engine = client
    test_client.post(
        "/hunts",
        data={"title": "Rundll32 abuse", "hypothesis": "Attackers abuse rundll32.", "priority": "2"},
        follow_redirects=False,
    )

    with Session(engine) as session:
        hunt = session.exec(select(Hunt)).first()
        assert hunt.query == ""
        assert hunt.attack_techniques == ""
        assert hunt.cadence_days is None


def test_created_hunt_has_server_generated_timestamps(client: tuple[TestClient, object]) -> None:
    test_client, engine = client
    test_client.post(
        "/hunts",
        data={"title": "Rundll32 abuse", "hypothesis": "Attackers abuse rundll32.", "priority": "2"},
        follow_redirects=False,
    )

    with Session(engine) as session:
        hunt = session.exec(select(Hunt)).first()
        assert hunt.created_at is not None
        assert hunt.updated_at is not None


def test_client_cannot_choose_a_different_initial_status(
    client: tuple[TestClient, object],
) -> None:
    test_client, engine = client
    test_client.post(
        "/hunts",
        data={
            "title": "Rundll32 abuse",
            "hypothesis": "Attackers abuse rundll32.",
            "priority": "2",
            "status": "active",
        },
        follow_redirects=False,
    )

    with Session(engine) as session:
        hunt = session.exec(select(Hunt)).first()
        assert hunt.status == HuntStatus.idea


def test_blank_title_is_rejected(client: tuple[TestClient, object]) -> None:
    test_client, engine = client
    response = test_client.post(
        "/hunts",
        data={"title": "", "hypothesis": "Attackers abuse rundll32.", "priority": "2"},
        follow_redirects=False,
    )

    assert response.status_code == 422
    with Session(engine) as session:
        assert session.exec(select(Hunt)).first() is None


def test_whitespace_only_title_is_rejected(client: tuple[TestClient, object]) -> None:
    test_client, engine = client
    response = test_client.post(
        "/hunts",
        data={"title": "   ", "hypothesis": "Attackers abuse rundll32.", "priority": "2"},
        follow_redirects=False,
    )

    assert response.status_code == 422
    with Session(engine) as session:
        assert session.exec(select(Hunt)).first() is None


def test_blank_hypothesis_is_rejected(client: tuple[TestClient, object]) -> None:
    test_client, engine = client
    response = test_client.post(
        "/hunts",
        data={"title": "Rundll32 abuse", "hypothesis": "", "priority": "2"},
        follow_redirects=False,
    )

    assert response.status_code == 422
    with Session(engine) as session:
        assert session.exec(select(Hunt)).first() is None


def test_whitespace_only_hypothesis_is_rejected(client: tuple[TestClient, object]) -> None:
    test_client, engine = client
    response = test_client.post(
        "/hunts",
        data={"title": "Rundll32 abuse", "hypothesis": "   ", "priority": "2"},
        follow_redirects=False,
    )

    assert response.status_code == 422
    with Session(engine) as session:
        assert session.exec(select(Hunt)).first() is None


def test_missing_priority_is_rejected(client: tuple[TestClient, object]) -> None:
    test_client, engine = client
    response = test_client.post(
        "/hunts",
        data={"title": "Rundll32 abuse", "hypothesis": "Attackers abuse rundll32."},
        follow_redirects=False,
    )

    assert response.status_code == 422
    with Session(engine) as session:
        assert session.exec(select(Hunt)).first() is None


def test_priority_below_one_is_rejected(client: tuple[TestClient, object]) -> None:
    test_client, engine = client
    response = test_client.post(
        "/hunts",
        data={"title": "Rundll32 abuse", "hypothesis": "Attackers abuse rundll32.", "priority": "0"},
        follow_redirects=False,
    )

    assert response.status_code == 422
    with Session(engine) as session:
        assert session.exec(select(Hunt)).first() is None


def test_priority_above_five_is_rejected(client: tuple[TestClient, object]) -> None:
    test_client, engine = client
    response = test_client.post(
        "/hunts",
        data={"title": "Rundll32 abuse", "hypothesis": "Attackers abuse rundll32.", "priority": "6"},
        follow_redirects=False,
    )

    assert response.status_code == 422
    with Session(engine) as session:
        assert session.exec(select(Hunt)).first() is None


def test_submitted_values_remain_populated_after_validation_failure(
    client: tuple[TestClient, object],
) -> None:
    test_client, _ = client
    response = test_client.post(
        "/hunts",
        data={"title": "Rundll32 abuse", "hypothesis": "", "priority": "2"},
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert "Rundll32 abuse" in response.text
    assert 'value="2" selected' in response.text or "selected" in response.text


def test_invalid_submission_shows_inline_validation_message(
    client: tuple[TestClient, object],
) -> None:
    test_client, _ = client
    response = test_client.post(
        "/hunts",
        data={"title": "Rundll32 abuse", "hypothesis": "", "priority": "2"},
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert "Hypothesis is required." in response.text
    assert "detail" not in response.text.lower()
