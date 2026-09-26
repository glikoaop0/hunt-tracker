from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models import Exclusion, Hunt, HuntStatus, Run, RunOutcome


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
        hunt = Hunt(
            title="Golden ticket forgery",
            hypothesis="Forged Kerberos tickets used for persistence",
            query="index=win_events EventCode=4768\n| where ticket_lifetime > 36000",
            status=HuntStatus.active,
            priority=1,
            data_sources="Windows Event Logs, Splunk",
            attack_techniques="T1558.001",
            notes="Some hunt notes.",
            cadence_days=14,
        )
        session.add(hunt)
        session.commit()
        session.refresh(hunt)

        session.add(
            Run(
                hunt_id=hunt.id,
                outcome=RunOutcome.findings,
                notes="Found a forged ticket.",
                query_snapshot="index=win_events EventCode=4768\n| where ticket_lifetime > 36000",
                result_count=1,
            )
        )
        session.add(Exclusion(hunt_id=hunt.id, value="dc-03", reason="Lab DC"))
        session.commit()
        hunt_id = hunt.id

    yield TestClient(app), hunt_id

    app.dependency_overrides.clear()


def test_export_markdown_headers_and_filename(client: tuple[TestClient, int]) -> None:
    test_client, hunt_id = client
    response = test_client.get(f"/hunts/{hunt_id}/export/markdown")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition
    assert ".md" in disposition
    assert "golden-ticket-forgery" in disposition


def test_export_markdown_contains_expected_sections(client: tuple[TestClient, int]) -> None:
    test_client, hunt_id = client
    body = test_client.get(f"/hunts/{hunt_id}/export/markdown").text

    assert "# Golden ticket forgery" in body
    assert "## Hypothesis" in body
    assert "Forged Kerberos tickets used for persistence" in body
    assert "## Data sources" in body
    assert "Windows Event Logs, Splunk" in body
    assert "## ATT&CK techniques" in body
    assert "T1558.001" in body
    assert "## Current query" in body
    assert "index=win_events EventCode=4768" in body
    assert "## Run history" in body
    assert "Found a forged ticket." in body
    assert "## Exclusions" in body
    assert "dc-03" in body
    assert "## Metadata" in body


def test_export_markdown_query_with_backticks_does_not_break_fence(
    client: tuple[TestClient, int],
) -> None:
    test_client, hunt_id = client
    tricky_query = "index=edr | eval x=\"```embedded fence```\""

    response = test_client.post(
        f"/hunts/{hunt_id}/edit",
        data={
            "title": "Golden ticket forgery",
            "hypothesis": "Forged Kerberos tickets used for persistence",
            "priority": "1",
            "query": tricky_query,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    body = test_client.get(f"/hunts/{hunt_id}/export/markdown").text
    assert tricky_query in body

    # The fence wrapping *this specific* query must be longer than the longest
    # backtick run inside it (3), so it can't be confused with the content.
    lines = body.splitlines()
    query_line_index = next(i for i, line in enumerate(lines) if line == tricky_query)
    opening_fence = lines[query_line_index - 1].strip()
    closing_fence = lines[query_line_index + 1].strip()
    assert opening_fence == closing_fence == "`" * 4


def test_export_markdown_returns_404_for_unknown_hunt(client: tuple[TestClient, int]) -> None:
    test_client, _ = client
    response = test_client.get("/hunts/999999/export/markdown")
    assert response.status_code == 404
