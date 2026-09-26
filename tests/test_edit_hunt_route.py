from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models import Exclusion, Hunt, HuntStatus, RetirementReason, Run, RunOutcome


@pytest.fixture()
def client() -> Iterator[tuple[TestClient, int, object]]:
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
            title="Golden ticket forgery",
            hypothesis="Forged Kerberos tickets used for persistence",
            query="index=win_events EventCode=4768",
            status=HuntStatus.active,
            priority=1,
            data_sources="Windows Event Logs",
            attack_techniques="T1558.001",
            notes="Some existing notes",
            cadence_days=14,
            next_run=date(2026, 1, 1),
        )
        session.add(hunt)
        session.commit()
        session.refresh(hunt)

        session.add(Run(hunt_id=hunt.id, outcome=RunOutcome.no_findings, notes="Clean pass."))
        session.add(Exclusion(hunt_id=hunt.id, value="dc-03", reason="Lab DC"))
        session.commit()

        hunt_id = hunt.id

    yield TestClient(app), hunt_id, engine

    app.dependency_overrides.clear()


def valid_payload(**overrides) -> dict:
    payload = {
        "title": "Updated title",
        "hypothesis": "Updated hypothesis",
        "priority": "2",
        "data_sources": "Zeek, Splunk",
        "attack_techniques": "T1218",
        "query": "index=dns",
        "notes": "Updated notes",
        "cadence_days": "30",
        "next_run": "2026-03-01",
    }
    payload.update(overrides)
    return payload


def test_edit_hunt_form_loads_for_existing_hunt(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, _ = client
    response = test_client.get(f"/hunts/{hunt_id}/edit")

    assert response.status_code == 200
    assert "<form" in response.text
    assert 'name="cadence_days"' in response.text
    assert 'name="next_run"' in response.text
    assert 'name="next_run"' in response.text


def test_edit_hunt_form_explains_query_snapshots(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, _ = client
    response = test_client.get(f"/hunts/{hunt_id}/edit")

    assert "Past runs are safe." in response.text
    assert "snapshot of the query" in response.text


def test_edit_hunt_form_returns_404_for_unknown_hunt(client: tuple[TestClient, int, object]) -> None:
    test_client, _, _ = client
    response = test_client.get("/hunts/999/edit")
    assert response.status_code == 404


def test_edit_hunt_form_prepopulates_existing_values(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, _ = client
    response = test_client.get(f"/hunts/{hunt_id}/edit")

    assert "Golden ticket forgery" in response.text
    assert "Forged Kerberos tickets used for persistence" in response.text
    assert "index=win_events EventCode=4768" in response.text
    assert "Windows Event Logs" in response.text
    assert "T1558.001" in response.text
    assert "Some existing notes" in response.text
    assert 'value="14"' in response.text
    assert 'value="2026-01-01"' in response.text


def test_edit_hunt_action_appears_on_detail_page(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, _ = client
    response = test_client.get(f"/hunts/{hunt_id}")

    assert f'href="/hunts/{hunt_id}/edit"' in response.text


def test_valid_update_persists_all_editable_fields(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    test_client.post(f"/hunts/{hunt_id}/edit", data=valid_payload(), follow_redirects=False)

    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.title == "Updated title"
        assert hunt.hypothesis == "Updated hypothesis"
        assert hunt.priority == 2
        assert hunt.data_sources == "Zeek, Splunk"
        assert hunt.attack_techniques == "T1218"
        assert hunt.query == "index=dns"
        assert hunt.notes == "Updated notes"
        assert hunt.cadence_days == 30
        assert hunt.next_run == date(2026, 3, 1)


def test_successful_update_returns_303_redirect_to_detail_page(
    client: tuple[TestClient, int, object],
) -> None:
    test_client, hunt_id, _ = client
    response = test_client.post(f"/hunts/{hunt_id}/edit", data=valid_payload(), follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/hunts/{hunt_id}"


def test_title_and_hypothesis_are_trimmed(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    test_client.post(
        f"/hunts/{hunt_id}/edit",
        data=valid_payload(title="  Padded title  ", hypothesis="  Padded hypothesis  "),
        follow_redirects=False,
    )

    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.title == "Padded title"
        assert hunt.hypothesis == "Padded hypothesis"


def test_blank_title_is_rejected(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    response = test_client.post(
        f"/hunts/{hunt_id}/edit", data=valid_payload(title=""), follow_redirects=False
    )

    assert response.status_code == 422
    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.title == "Golden ticket forgery"


def test_whitespace_only_title_is_rejected(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    response = test_client.post(
        f"/hunts/{hunt_id}/edit", data=valid_payload(title="   "), follow_redirects=False
    )

    assert response.status_code == 422
    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.title == "Golden ticket forgery"


def test_blank_hypothesis_is_rejected(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    response = test_client.post(
        f"/hunts/{hunt_id}/edit", data=valid_payload(hypothesis=""), follow_redirects=False
    )

    assert response.status_code == 422
    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.hypothesis == "Forged Kerberos tickets used for persistence"


def test_whitespace_only_hypothesis_is_rejected(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    response = test_client.post(
        f"/hunts/{hunt_id}/edit", data=valid_payload(hypothesis="   "), follow_redirects=False
    )

    assert response.status_code == 422
    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.hypothesis == "Forged Kerberos tickets used for persistence"


def test_priority_below_one_is_rejected(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    response = test_client.post(
        f"/hunts/{hunt_id}/edit", data=valid_payload(priority="0"), follow_redirects=False
    )

    assert response.status_code == 422
    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.priority == 1


def test_priority_above_five_is_rejected(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    response = test_client.post(
        f"/hunts/{hunt_id}/edit", data=valid_payload(priority="6"), follow_redirects=False
    )

    assert response.status_code == 422
    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.priority == 1


def test_invalid_cadence_is_rejected(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    response = test_client.post(
        f"/hunts/{hunt_id}/edit", data=valid_payload(cadence_days="not-a-number"), follow_redirects=False
    )

    assert response.status_code == 422
    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.cadence_days == 14


def test_negative_cadence_is_rejected(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    response = test_client.post(
        f"/hunts/{hunt_id}/edit", data=valid_payload(cadence_days="-5"), follow_redirects=False
    )

    assert response.status_code == 422
    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.cadence_days == 14


def test_submitted_values_remain_visible_after_validation_failure(
    client: tuple[TestClient, int, object],
) -> None:
    test_client, hunt_id, _ = client
    response = test_client.post(
        f"/hunts/{hunt_id}/edit",
        data=valid_payload(title="Kept title", hypothesis=""),
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert "Kept title" in response.text
    assert "Zeek, Splunk" in response.text


def test_invalid_submission_does_not_partially_update(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    test_client.post(
        f"/hunts/{hunt_id}/edit",
        data=valid_payload(title="Should not be saved", hypothesis=""),
        follow_redirects=False,
    )

    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.title == "Golden ticket forgery"
        assert hunt.data_sources == "Windows Event Logs"


def test_created_at_remains_unchanged_after_update(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    with Session(engine) as session:
        original_created_at = session.get(Hunt, hunt_id).created_at

    test_client.post(f"/hunts/{hunt_id}/edit", data=valid_payload(), follow_redirects=False)

    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.created_at == original_created_at


def test_updated_at_changes_after_successful_update(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    with Session(engine) as session:
        original_updated_at = session.get(Hunt, hunt_id).updated_at

    test_client.post(f"/hunts/{hunt_id}/edit", data=valid_payload(), follow_redirects=False)

    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.updated_at > original_updated_at


def test_status_cannot_be_changed_through_edit_endpoint(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    test_client.post(
        f"/hunts/{hunt_id}/edit", data=valid_payload(status="retired"), follow_redirects=False
    )

    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.status == HuntStatus.active


def test_retirement_metadata_cannot_be_changed_through_edit_endpoint(
    client: tuple[TestClient, int, object],
) -> None:
    test_client, hunt_id, engine = client
    test_client.post(
        f"/hunts/{hunt_id}/edit",
        data=valid_payload(retirement_reason="superseded", retirement_note="hijacked"),
        follow_redirects=False,
    )

    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.retirement_reason is None
        assert hunt.retirement_note is None
        assert hunt.retired_at is None


def test_existing_runs_and_exclusions_are_preserved(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    test_client.post(f"/hunts/{hunt_id}/edit", data=valid_payload(), follow_redirects=False)

    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.run_count == 1
        assert len(hunt.active_exclusions) == 1


def test_optional_fields_can_be_cleared_intentionally(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    test_client.post(
        f"/hunts/{hunt_id}/edit",
        data=valid_payload(data_sources="", attack_techniques="", query="", notes="", cadence_days=""),
        follow_redirects=False,
    )

    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.data_sources == ""
        assert hunt.attack_techniques == ""
        assert hunt.query == ""
        assert hunt.notes == ""
        assert hunt.cadence_days is None


def test_query_preserves_multiline_and_special_characters(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    tricky_query = 'index=edr | where match(cmd, "a\\"b")\nstats count by host'
    test_client.post(f"/hunts/{hunt_id}/edit", data=valid_payload(query=tricky_query), follow_redirects=False)

    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.query == tricky_query


def test_detail_page_displays_updated_values(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, _ = client
    test_client.post(f"/hunts/{hunt_id}/edit", data=valid_payload(), follow_redirects=False)

    response = test_client.get(f"/hunts/{hunt_id}")
    assert "Updated title" in response.text
    assert "Updated hypothesis" in response.text
    assert "index=dns" in response.text


def test_clearing_cadence_also_clears_next_run(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    test_client.post(
        f"/hunts/{hunt_id}/edit",
        data=valid_payload(cadence_days="", next_run="2026-05-01"),
        follow_redirects=False,
    )

    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.cadence_days is None
        assert hunt.next_run is None


def test_valid_next_run_date_update(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    test_client.post(
        f"/hunts/{hunt_id}/edit", data=valid_payload(next_run="2026-04-15"), follow_redirects=False
    )

    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.next_run == date(2026, 4, 15)


def test_blank_next_run_is_accepted(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    test_client.post(f"/hunts/{hunt_id}/edit", data=valid_payload(next_run=""), follow_redirects=False)

    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.next_run is None


def test_invalid_next_run_is_rejected(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    response = test_client.post(
        f"/hunts/{hunt_id}/edit", data=valid_payload(next_run="not-a-date"), follow_redirects=False
    )

    assert response.status_code == 422
    with Session(engine) as session:
        hunt = session.get(Hunt, hunt_id)
        assert hunt.next_run == date(2026, 1, 1)
