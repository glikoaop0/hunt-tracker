import json
from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models import Exclusion, Hunt, HuntStatus, RetirementReason, Run, RunOutcome


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
            title="Golden ticket forgery",
            hypothesis="Forged Kerberos tickets",
            query="index=win_events EventCode=4768",
            status=HuntStatus.retired,
            priority=1,
            cadence_days=14,
            next_run=date(2026, 2, 1),
            retirement_reason=RetirementReason.converted_to_detection,
            retirement_note="Now a rule.",
        )
        session.add(hunt)
        session.commit()
        session.refresh(hunt)

        run = Run(
            hunt_id=hunt.id,
            outcome=RunOutcome.findings,
            notes="Found it.",
            query_snapshot="index=win_events EventCode=4768",
            result_count=3,
        )
        session.add(run)
        exclusion = Exclusion(hunt_id=hunt.id, value="dc-03", reason="Lab DC")
        session.add(exclusion)
        session.commit()

    yield TestClient(app)

    app.dependency_overrides.clear()


def test_export_json_headers_and_filename(client: TestClient) -> None:
    response = client.get("/export/json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition
    assert ".json" in disposition


def test_export_json_has_schema_version_and_metadata(client: TestClient) -> None:
    payload = client.get("/export/json").json()

    assert payload["export_schema_version"] == 1
    assert "exported_at" in payload
    assert payload["application"] == "Hunt Tracker"


def test_export_json_nests_runs_and_exclusions_completely(client: TestClient) -> None:
    payload = client.get("/export/json").json()

    assert len(payload["hunts"]) == 1
    hunt = payload["hunts"][0]
    assert hunt["title"] == "Golden ticket forgery"
    assert len(hunt["runs"]) == 1
    assert hunt["runs"][0]["outcome"] == "findings"
    assert hunt["runs"][0]["query_snapshot"] == "index=win_events EventCode=4768"
    assert hunt["runs"][0]["result_count"] == 3
    assert len(hunt["exclusions"]) == 1
    assert hunt["exclusions"][0]["value"] == "dc-03"
    assert hunt["exclusions"][0]["active"] is True


def test_export_json_enums_and_dates_are_stable_strings(client: TestClient) -> None:
    payload = client.get("/export/json").json()
    hunt = payload["hunts"][0]

    assert hunt["status"] == "retired"
    assert hunt["retirement_reason"] == "converted_to_detection"
    assert hunt["next_run"] == "2026-02-01"
    assert hunt["runs"][0]["outcome"] == "findings"
    # ISO 8601 datetimes should parse back cleanly
    from datetime import datetime

    datetime.fromisoformat(hunt["created_at"])
    datetime.fromisoformat(hunt["runs"][0]["ran_at"])


def test_export_json_preserves_null_fields(client: TestClient) -> None:
    payload = client.get("/export/json").json()
    hunt = payload["hunts"][0]

    assert hunt["runs"][0]["search_from"] is None
    assert hunt["runs"][0]["search_to"] is None
    assert hunt["runs"][0]["duration_minutes"] is None


def test_export_json_is_valid_json_text(client: TestClient) -> None:
    response = client.get("/export/json")
    parsed = json.loads(response.text)
    assert isinstance(parsed["hunts"], list)
