from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models import Hunt, HuntStatus


@pytest.fixture()
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    with Session(engine) as session:
        hunt = Hunt(
            title="Suspicious OAuth app consent grants",
            hypothesis="Broad OAuth scope grants to malicious apps",
            query="index=m365_audit | search VERY_SECRET_INTERNAL_TOKEN=xyz123",
            status=HuntStatus.active,
            priority=2,
            data_sources="M365 Audit Logs",
            attack_techniques="T1528",
        )
        session.add(hunt)
        session.commit()

    yield TestClient(app)

    app.dependency_overrides.clear()


def test_filter_bar_appears_once_outside_board(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.text.count('id="filter-bar"') == 1
    filter_bar_index = response.text.index('id="filter-bar"')
    board_index = response.text.index('id="board"')
    assert filter_bar_index < board_index


def test_filter_controls_are_present(client: TestClient) -> None:
    body = client.get("/").text

    assert 'id="filter-search"' in body
    assert 'id="filter-priority"' in body
    assert 'id="filter-overdue"' in body
    assert 'id="filter-clear"' in body
    assert 'aria-live="polite"' in body


def test_cards_expose_safe_filter_data_attributes(client: TestClient) -> None:
    body = client.get("/").text

    assert 'data-priority="2"' in body
    assert 'data-overdue="false"' in body
    assert "data-search=" in body
    assert "suspicious oauth app consent grants" in body.lower()


def test_query_text_is_not_copied_into_search_attribute(client: TestClient) -> None:
    body = client.get("/").text

    # The query text must never appear inside the data-search attribute value.
    start = body.index('data-search="')
    end = body.index('"', start + len('data-search="'))
    search_value = body[start:end]
    assert "very_secret_internal_token" not in search_value.lower()


def test_existing_drag_and_drop_attributes_are_unchanged(client: TestClient) -> None:
    body = client.get("/").text

    assert 'draggable="true"' in body
    assert "data-hunt-id=" in body
    assert 'data-status="idea"' in body
    assert 'data-status="scoped"' in body
    assert 'data-status="active"' in body
    assert 'data-status="retired"' in body
    assert 'class="cards"' in body


def test_summary_and_board_ids_are_unchanged(client: TestClient) -> None:
    body = client.get("/").text

    assert 'id="board"' in body
    assert 'id="summary-strip"' in body
    assert 'id="retire-dialog-root"' in body
