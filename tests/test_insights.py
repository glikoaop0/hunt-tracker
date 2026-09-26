from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.insights import build_insights, resolve_range
from app.main import app
from app.models import Exclusion, Hunt, HuntStatus, RetirementReason, Run, RunOutcome


@pytest.fixture()
def seeded_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        active_hunt = Hunt(title="Active hunt", hypothesis="h", status=HuntStatus.active, priority=1)
        idea_hunt = Hunt(title="Idea hunt", hypothesis="h", status=HuntStatus.idea, priority=3)
        overdue_hunt = Hunt(
            title="Overdue hunt",
            hypothesis="h",
            status=HuntStatus.active,
            priority=1,
            cadence_days=7,
            next_run=date.today() - timedelta(days=1),
        )
        retired_detection = Hunt(
            title="Retired via detection",
            hypothesis="h",
            status=HuntStatus.retired,
            retirement_reason=RetirementReason.converted_to_detection,
        )
        retired_other = Hunt(
            title="Retired other",
            hypothesis="h",
            status=HuntStatus.retired,
            retirement_reason=RetirementReason.no_longer_relevant,
        )
        session.add_all([active_hunt, idea_hunt, overdue_hunt, retired_detection, retired_other])
        session.commit()
        for hunt in (active_hunt, idea_hunt, overdue_hunt, retired_detection, retired_other):
            session.refresh(hunt)

        recent_run = Run(
            hunt_id=active_hunt.id,
            outcome=RunOutcome.findings,
            ran_at=datetime.now(UTC) - timedelta(days=1),
            result_count=5,
        )
        old_run = Run(
            hunt_id=active_hunt.id,
            outcome=RunOutcome.no_findings,
            ran_at=datetime.now(UTC) - timedelta(days=200),
        )
        session.add_all([recent_run, old_run])

        session.add(Exclusion(hunt_id=active_hunt.id, value="a", reason="r", active=True))
        session.add(Exclusion(hunt_id=active_hunt.id, value="b", reason="r", active=False))
        session.commit()

    with Session(engine) as session:
        yield session


def test_resolve_range_accepts_known_values() -> None:
    assert resolve_range("7") == "7"
    assert resolve_range("30") == "30"
    assert resolve_range("90") == "90"
    assert resolve_range("all") == "all"


def test_resolve_range_falls_back_safely_for_unknown_value() -> None:
    assert resolve_range("banana") == "30"
    assert resolve_range(None) == "30"
    assert resolve_range("") == "30"


def test_lifecycle_counts_are_correct(seeded_session: Session) -> None:
    data = build_insights(seeded_session, "all")
    counts = dict(data["lifecycle_rows"])

    assert counts[HuntStatus.idea] == 1
    assert counts[HuntStatus.active] == 2
    assert counts[HuntStatus.retired] == 2
    assert data["total_hunts"] == 5
    assert data["active_hunts"] == 2


def test_overdue_count_uses_is_overdue_rule(seeded_session: Session) -> None:
    data = build_insights(seeded_session, "all")
    assert data["overdue_hunts"] == 1


def test_converted_to_detection_count(seeded_session: Session) -> None:
    data = build_insights(seeded_session, "all")
    assert data["converted_to_detection"] == 1


def test_active_exclusion_count(seeded_session: Session) -> None:
    data = build_insights(seeded_session, "all")
    assert data["active_exclusions"] == 1


def test_run_outcome_counts_respect_selected_range(seeded_session: Session) -> None:
    all_time = build_insights(seeded_session, "all")
    assert all_time["runs_in_period_count"] == 2

    last_7_days = build_insights(seeded_session, "7")
    assert last_7_days["runs_in_period_count"] == 1
    outcome_counts = dict(last_7_days["outcome_rows"])
    assert outcome_counts[RunOutcome.findings] == 1
    assert outcome_counts[RunOutcome.no_findings] == 0


def test_retirement_counts_only_include_reasons_present(seeded_session: Session) -> None:
    data = build_insights(seeded_session, "all")
    reasons = {reason for reason, _ in data["retirement_rows"]}

    assert reasons == {RetirementReason.converted_to_detection, RetirementReason.no_longer_relevant}
    counts = dict(data["retirement_rows"])
    assert counts[RetirementReason.converted_to_detection] == 1


def test_recent_activity_is_reverse_chronological(seeded_session: Session) -> None:
    data = build_insights(seeded_session, "all")
    ran_ats = [run.ran_at for run in data["recent_runs"]]
    assert ran_ats == sorted(ran_ats, reverse=True)


def test_recent_activity_is_independent_of_range(seeded_session: Session) -> None:
    data_7 = build_insights(seeded_session, "7")
    data_all = build_insights(seeded_session, "all")

    assert len(data_7["recent_runs"]) == len(data_all["recent_runs"]) == 2


def test_empty_database_renders_safely() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        data = build_insights(session, "30")

    assert data["total_hunts"] == 0
    assert data["lifecycle_max"] == 1  # never zero, to avoid a divide-by-zero in the template
    assert data["retirement_rows"] == []
    assert data["recent_runs"] == []


def test_zero_data_period_renders_safely(seeded_session: Session) -> None:
    data = build_insights(seeded_session, "7")
    # Only the very recent run falls in a 7-day window; outcome bars must
    # still render with explicit zero counts, not a broken/empty chart.
    outcome_counts = dict(data["outcome_rows"])
    assert outcome_counts[RunOutcome.inconclusive] == 0
    assert outcome_counts[RunOutcome.detection_opportunity] == 0


# --- Route-level tests ---


@pytest.fixture()
def client() -> Iterator[tuple[TestClient, Session]]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    with Session(engine) as session:
        hunt = Hunt(title="Linked hunt", hypothesis="h", status=HuntStatus.active, priority=2)
        session.add(hunt)
        session.commit()
        session.refresh(hunt)
        session.add(Run(hunt_id=hunt.id, outcome=RunOutcome.findings, result_count=1))
        session.commit()
        hunt_id = hunt.id

    yield TestClient(app), hunt_id, engine

    app.dependency_overrides.clear()


def test_insights_route_renders(client: tuple[TestClient, int, object]) -> None:
    test_client, *_ = client
    response = test_client.get("/insights")
    assert response.status_code == 200
    assert "Insights" in response.text


def test_date_range_options_work(client: tuple[TestClient, int, object]) -> None:
    test_client, *_ = client
    for range_value in ("7", "30", "90", "all"):
        response = test_client.get("/insights", params={"range": range_value})
        assert response.status_code == 200


def test_invalid_range_falls_back_safely(client: tuple[TestClient, int, object]) -> None:
    test_client, *_ = client
    response = test_client.get("/insights", params={"range": "not-a-range"})
    assert response.status_code == 200
    assert "Insights" in response.text


def test_recent_activity_links_point_to_correct_hunt(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, _ = client
    response = test_client.get("/insights")
    assert f'href="/hunts/{hunt_id}"' in response.text
    assert "Linked hunt" in response.text


def test_insights_page_does_not_mutate_data(client: tuple[TestClient, int, object]) -> None:
    test_client, hunt_id, engine = client
    with Session(engine) as session:
        before = session.get(Hunt, hunt_id).updated_at

    test_client.get("/insights")
    test_client.get("/insights", params={"range": "7"})

    with Session(engine) as session:
        after = session.get(Hunt, hunt_id).updated_at
        run_count = len(session.exec(select(Run)).all())

    assert before == after
    assert run_count == 1
