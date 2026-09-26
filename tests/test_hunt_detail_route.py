import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models import Hunt, HuntStatus, RetirementReason, Run, RunOutcome


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
            title="DNS beaconing to rare domains",
            hypothesis="C2 beacons hidden in DNS queries",
            query="index=dns | stats count by query_domain",
            status=HuntStatus.active,
            priority=4,
            data_sources="Splunk, CrowdStrike",
            attack_techniques="T1071.004",
            cadence_days=14,
            next_run=date(2026, 1, 1),
        )
        session.add(hunt)
        session.commit()
        session.refresh(hunt)

        run = Run(
            hunt_id=hunt.id,
            outcome=RunOutcome.findings,
            notes="Beacon interval **observed** at 60s.\nSecond line of detail.",
            duration_minutes=45,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        no_cadence_hunt = Hunt(
            title="One-off macro review",
            hypothesis="Check a suspicious macro sample",
            status=HuntStatus.idea,
        )
        session.add(no_cadence_hunt)
        session.commit()
        session.refresh(no_cadence_hunt)

        hunt_id, run_id, no_cadence_hunt_id = hunt.id, run.id, no_cadence_hunt.id

    yield TestClient(app), hunt_id, run_id, no_cadence_hunt_id

    app.dependency_overrides.clear()


def test_hunt_detail_returns_404_for_unknown_id(client: tuple[TestClient, int, int, int]) -> None:
    test_client, _, _, _ = client
    response = test_client.get("/hunts/999")
    assert response.status_code == 404


def test_hunt_detail_shows_header_hypothesis_query_and_metadata(
    client: tuple[TestClient, int, int, int],
) -> None:
    test_client, hunt_id, _, _ = client
    response = test_client.get(f"/hunts/{hunt_id}")

    assert response.status_code == 200
    assert "DNS beaconing to rare domains" in response.text
    assert "C2 beacons hidden in DNS queries" in response.text
    assert "index=dns | stats count by query_domain" in response.text
    assert "Active" in response.text
    assert "1 run" in response.text
    assert "Splunk" in response.text
    assert "CrowdStrike" in response.text
    assert "T1071.004" in response.text


def test_hunt_detail_shows_collapsed_run_with_notes_preview(
    client: tuple[TestClient, int, int, int],
) -> None:
    test_client, hunt_id, _, _ = client
    response = test_client.get(f"/hunts/{hunt_id}")

    assert "Findings" in response.text
    assert "Beacon interval **observed** at 60s." in response.text
    assert "Second line of detail." not in response.text


def test_run_row_expand_shows_full_markdown_notes(client: tuple[TestClient, int, int, int]) -> None:
    test_client, hunt_id, run_id, _ = client
    response = test_client.get(f"/hunts/{hunt_id}/runs/{run_id}/row", params={"expanded": "true"})

    assert response.status_code == 200
    assert "<strong>observed</strong>" in response.text
    assert "Second line of detail." in response.text
    assert "45 min" in response.text


def test_collapsed_run_row_shows_formatted_outcome_label(
    client: tuple[TestClient, int, int, int],
) -> None:
    test_client, hunt_id, _, _ = client
    response = test_client.post(
        f"/hunts/{hunt_id}/runs",
        data={"outcome": "detection_opportunity", "notes": "Shipped a standing detection."},
    )

    assert response.status_code == 200
    assert "Detection opportunity" in response.text


def test_expanded_run_row_shows_formatted_outcome_label(
    client: tuple[TestClient, int, int, int],
) -> None:
    test_client, hunt_id, _, _ = client
    create_response = test_client.post(
        f"/hunts/{hunt_id}/runs",
        data={"outcome": "no_findings", "notes": "Clean pass."},
    )
    match = re.search(r'id="run-(\d+)"', create_response.text)
    assert match is not None
    run_id = match.group(1)

    response = test_client.get(f"/hunts/{hunt_id}/runs/{run_id}/row", params={"expanded": "true"})

    assert response.status_code == 200
    assert "No findings" in response.text
    assert "no_findings" not in response.text


def test_create_run_with_cadence_advances_next_run(client: tuple[TestClient, int, int, int]) -> None:
    test_client, hunt_id, _, _ = client
    response = test_client.post(
        f"/hunts/{hunt_id}/runs",
        data={"outcome": "no_findings", "notes": "Clean re-run.", "duration_minutes": "20"},
    )

    assert response.status_code == 200
    assert (date(2026, 1, 1) + timedelta(days=14)).isoformat() in response.text
    assert "2 runs" in response.text
    assert "Clean re-run." in response.text


def test_create_run_without_cadence_leaves_next_run_unset(
    client: tuple[TestClient, int, int, int],
) -> None:
    test_client, _, _, no_cadence_hunt_id = client
    response = test_client.post(
        f"/hunts/{no_cadence_hunt_id}/runs",
        data={"outcome": "inconclusive", "notes": "First look."},
    )

    assert response.status_code == 200
    assert "No cadence set" in response.text
    assert "1 run" in response.text


def test_hunt_detail_with_cadence_but_no_next_run_says_not_scheduled_yet(
    client: tuple[TestClient, int, int, int],
) -> None:
    test_client, _, _, no_cadence_hunt_id = client
    edit = test_client.post(
        f"/hunts/{no_cadence_hunt_id}/edit",
        data={
            "title": "One-off macro review",
            "hypothesis": "Check a suspicious macro sample",
            "priority": "3",
            "cadence_days": "7",
            "next_run": "",
        },
        follow_redirects=False,
    )
    assert edit.status_code in (200, 303)

    response = test_client.get(f"/hunts/{no_cadence_hunt_id}")

    assert "Not scheduled yet" in response.text
    assert "No cadence set" not in response.text


def test_create_exclusion_adds_active_row(client: tuple[TestClient, int, int, int]) -> None:
    test_client, hunt_id, _, _ = client
    response = test_client.post(
        f"/hunts/{hunt_id}/exclusions",
        data={"value": "*.doubleclick.net", "reason": "Ad-tech noise"},
    )

    assert response.status_code == 200
    assert "*.doubleclick.net" in response.text
    assert "Ad-tech noise" in response.text


def test_deactivate_exclusion_removes_it_from_active_table(
    client: tuple[TestClient, int, int, int],
) -> None:
    test_client, hunt_id, _, _ = client
    add_response = test_client.post(
        f"/hunts/{hunt_id}/exclusions",
        data={"value": "*.adnxs.com", "reason": "Ad-tech noise"},
    )
    assert "*.adnxs.com" in add_response.text

    match = re.search(r"/hunts/\d+/exclusions/(\d+)/deactivate", add_response.text)
    assert match is not None
    exclusion_id = match.group(1)

    deactivate_response = test_client.post(f"/hunts/{hunt_id}/exclusions/{exclusion_id}/deactivate")

    assert deactivate_response.status_code == 200
    assert "*.adnxs.com" not in deactivate_response.text
    assert "No active exclusions." in deactivate_response.text


@pytest.fixture()
def retirement_client() -> Iterator[tuple[TestClient, int, int, int, int]]:
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
        never_retired = Hunt(
            title="DNS beaconing to rare domains",
            hypothesis="C2 beacons hidden in DNS queries",
            status=HuntStatus.active,
        )
        legacy_retired = Hunt(
            title="Legacy retired hunt",
            hypothesis="Predates the retirement-reason feature",
            status=HuntStatus.retired,
            retirement_reason=None,
            retirement_note=None,
            retired_at=None,
        )
        currently_retired = Hunt(
            title="Suspicious OAuth app consent grants",
            hypothesis="Broad OAuth scope grants to malicious apps",
            status=HuntStatus.retired,
            retirement_reason=RetirementReason.converted_to_detection,
            retirement_note="Now a standing detection.",
            retired_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        reactivated = Hunt(
            title="Reactivated hunt",
            hypothesis="Was retired, then brought back",
            status=HuntStatus.active,
            retirement_reason=RetirementReason.hypothesis_rejected,
            retirement_note="Turned out to be noise.",
            retired_at=datetime(2025, 6, 1, tzinfo=UTC),
        )
        session.add_all([never_retired, legacy_retired, currently_retired, reactivated])
        session.commit()
        for hunt in (never_retired, legacy_retired, currently_retired, reactivated):
            session.refresh(hunt)
        ids = (never_retired.id, legacy_retired.id, currently_retired.id, reactivated.id)

    yield TestClient(app), *ids

    app.dependency_overrides.clear()


def test_hunt_detail_hides_retirement_section_for_never_retired_hunt(
    retirement_client: tuple[TestClient, int, int, int, int],
) -> None:
    test_client, never_retired_id, _, _, _ = retirement_client
    response = test_client.get(f"/hunts/{never_retired_id}")

    assert response.status_code == 200
    assert "No reason recorded" not in response.text
    assert "Previous retirement" not in response.text
    assert '<section class="retirement">' not in response.text


def test_hunt_detail_shows_no_reason_recorded_for_legacy_retired_hunt(
    retirement_client: tuple[TestClient, int, int, int, int],
) -> None:
    test_client, _, legacy_retired_id, _, _ = retirement_client
    response = test_client.get(f"/hunts/{legacy_retired_id}")

    assert response.status_code == 200
    assert "No reason recorded" in response.text


def test_hunt_detail_shows_current_retirement_section(
    retirement_client: tuple[TestClient, int, int, int, int],
) -> None:
    test_client, _, _, currently_retired_id, _ = retirement_client
    response = test_client.get(f"/hunts/{currently_retired_id}")

    assert response.status_code == 200
    assert "<h2>Retirement</h2>" in response.text
    assert "Converted to detection" in response.text
    assert "2026-01-01" in response.text
    assert "Now a standing detection." in response.text


def test_hunt_detail_shows_previous_retirement_after_reactivation(
    retirement_client: tuple[TestClient, int, int, int, int],
) -> None:
    test_client, _, _, _, reactivated_id = retirement_client
    response = test_client.get(f"/hunts/{reactivated_id}")

    assert response.status_code == 200
    assert "Previous retirement" in response.text
    assert "Hypothesis rejected" in response.text
    assert "Turned out to be noise." in response.text


def test_retired_hunt_shows_banner_and_hides_run_and_exclusion_forms(
    retirement_client: tuple[TestClient, int, int, int, int],
) -> None:
    test_client, _, _, currently_retired_id, _ = retirement_client
    response = test_client.get(f"/hunts/{currently_retired_id}")

    assert response.status_code == 200
    assert 'class="retired-banner"' in response.text
    assert "Retired: <strong>Converted to detection</strong>" in response.text
    assert f'hx-post="/hunts/{currently_retired_id}/reactivate"' in response.text
    assert "Log a run" not in response.text
    assert f'hx-post="/hunts/{currently_retired_id}/runs"' not in response.text
    assert f'hx-post="/hunts/{currently_retired_id}/exclusions"' not in response.text


def test_legacy_retired_hunt_banner_says_no_reason_recorded(
    retirement_client: tuple[TestClient, int, int, int, int],
) -> None:
    test_client, _, legacy_retired_id, _, _ = retirement_client
    response = test_client.get(f"/hunts/{legacy_retired_id}")

    assert "Retired: <strong>No reason recorded</strong>" in response.text


def test_active_hunt_has_no_banner_and_keeps_forms(
    retirement_client: tuple[TestClient, int, int, int, int],
) -> None:
    test_client, never_retired_id, _, _, _ = retirement_client
    response = test_client.get(f"/hunts/{never_retired_id}")

    assert 'class="retired-banner"' not in response.text
    assert "Log a run" in response.text
    assert f'hx-post="/hunts/{never_retired_id}/exclusions"' in response.text


def test_reactivate_route_restores_forms_and_keeps_previous_retirement(
    retirement_client: tuple[TestClient, int, int, int, int],
) -> None:
    test_client, _, _, currently_retired_id, _ = retirement_client
    response = test_client.post(f"/hunts/{currently_retired_id}/reactivate")

    assert response.status_code == 200
    assert 'class="retired-banner"' not in response.text
    assert "Log a run" in response.text
    assert f'hx-post="/hunts/{currently_retired_id}/exclusions"' in response.text
    assert "Previous retirement" in response.text
    assert "Converted to detection" in response.text
    assert ">Active</span>" in response.text


def test_reactivation_is_persisted_across_page_loads(
    retirement_client: tuple[TestClient, int, int, int, int],
) -> None:
    test_client, _, _, currently_retired_id, _ = retirement_client
    test_client.post(f"/hunts/{currently_retired_id}/reactivate")

    detail = test_client.get(f"/hunts/{currently_retired_id}")
    board = test_client.get("/")

    assert ">Active</span>" in detail.text
    assert "<h2>Previous retirement</h2>" in detail.text
    assert "Now a standing detection." in detail.text
    active_column = board.text.split('data-status="active"', 1)[1].split('data-status="retired"', 1)[0]
    assert f'data-hunt-id="{currently_retired_id}"' in active_column


def test_reactivate_route_rejects_hunt_that_is_not_retired(
    retirement_client: tuple[TestClient, int, int, int, int],
) -> None:
    test_client, never_retired_id, _, _, _ = retirement_client
    response = test_client.post(f"/hunts/{never_retired_id}/reactivate")

    assert response.status_code == 400


def test_reactivate_route_returns_404_for_unknown_hunt(
    retirement_client: tuple[TestClient, int, int, int, int],
) -> None:
    test_client, _, _, _, _ = retirement_client
    response = test_client.post("/hunts/999/reactivate")

    assert response.status_code == 404
