from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models import Hunt, HuntStatus


@pytest.fixture()
def client() -> Iterator[tuple[TestClient, int]]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    with Session(engine) as session:
        hunt = Hunt(title="Test hunt", hypothesis="Test hypothesis", status=HuntStatus.idea)
        session.add(hunt)
        session.commit()
        session.refresh(hunt)
        hunt_id = hunt.id

    yield TestClient(app), hunt_id

    app.dependency_overrides.clear()


def test_export_page_renders(client: tuple[TestClient, int]) -> None:
    test_client, _ = client
    response = test_client.get("/export")

    assert response.status_code == 200
    assert 'href="/export/json"' in response.text
    assert 'href="/export/backup"' in response.text


def test_export_link_appears_in_shared_topbar(client: tuple[TestClient, int]) -> None:
    test_client, hunt_id = client
    board_response = test_client.get("/")
    detail_response = test_client.get(f"/hunts/{hunt_id}")

    assert 'href="/export"' in board_response.text
    assert 'href="/export"' in detail_response.text


def test_export_markdown_action_appears_on_hunt_detail(client: tuple[TestClient, int]) -> None:
    test_client, hunt_id = client
    response = test_client.get(f"/hunts/{hunt_id}")

    assert f'href="/hunts/{hunt_id}/export/markdown"' in response.text
    assert "Export Markdown" in response.text
