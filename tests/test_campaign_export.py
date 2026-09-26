from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models import Campaign, CampaignHunt, Hunt, HuntStatus


@pytest.fixture()
def client() -> Iterator[TestClient]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def override_get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    with Session(engine) as session:
        hunt = Hunt(title="Hunt one", hypothesis="H", status=HuntStatus.active)
        session.add(hunt)
        session.commit()
        session.refresh(hunt)

        campaign = Campaign(
            title="Export Campaign",
            objective="Make sure exports include campaigns.",
            scope="Everything",
            notes="Some notes",
            conclusion="Some conclusion",
        )
        session.add(campaign)
        session.commit()
        session.refresh(campaign)

        session.add(CampaignHunt(campaign_id=campaign.id, hunt_id=hunt.id))
        session.commit()

    yield TestClient(app)
    app.dependency_overrides.clear()


def test_export_json_includes_campaigns(client: TestClient) -> None:
    payload = client.get("/export/json").json()

    assert "campaigns" in payload
    assert len(payload["campaigns"]) == 1
    campaign = payload["campaigns"][0]
    assert campaign["title"] == "Export Campaign"
    assert campaign["objective"] == "Make sure exports include campaigns."
    assert campaign["status"] == "planned"


def test_export_json_includes_campaign_memberships(client: TestClient) -> None:
    payload = client.get("/export/json").json()
    campaign = payload["campaigns"][0]

    assert len(campaign["memberships"]) == 1
    assert campaign["memberships"][0]["hunt_id"] == 1
    from datetime import datetime

    datetime.fromisoformat(campaign["memberships"][0]["created_at"])


def test_export_json_still_includes_existing_hunt_fields(client: TestClient) -> None:
    payload = client.get("/export/json").json()

    assert len(payload["hunts"]) == 1
    hunt = payload["hunts"][0]
    assert hunt["title"] == "Hunt one"
    assert "runs" in hunt
    assert "exclusions" in hunt
