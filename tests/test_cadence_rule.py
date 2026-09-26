from datetime import UTC, date, datetime, timedelta

from app.models import Hunt, HuntStatus, RunOutcome
from app.services import log_run


def make_hunt(**overrides) -> Hunt:
    defaults = dict(title="Test hunt", hypothesis="Test hypothesis", status=HuntStatus.active)
    defaults.update(overrides)
    return Hunt(**defaults)


def test_completing_a_run_advances_next_run_by_cadence_days() -> None:
    hunt = make_hunt(cadence_days=14, next_run=date(2026, 1, 1))

    log_run(hunt, RunOutcome.no_findings)

    assert hunt.next_run == date(2026, 1, 15)


def test_completing_first_run_with_no_prior_next_run_uses_ran_at_as_base() -> None:
    hunt = make_hunt(cadence_days=7, next_run=None)

    run = log_run(hunt, RunOutcome.no_findings)

    assert hunt.next_run == run.ran_at.date() + timedelta(days=7)


def test_completing_a_run_without_cadence_leaves_next_run_untouched() -> None:
    hunt = make_hunt(cadence_days=None, next_run=None)

    log_run(hunt, RunOutcome.no_findings)

    assert hunt.next_run is None


def test_completing_a_run_updates_hunt_updated_at() -> None:
    hunt = make_hunt()
    stale_updated_at = datetime(2020, 1, 1, tzinfo=UTC)
    hunt.updated_at = stale_updated_at

    log_run(hunt, RunOutcome.findings)

    assert hunt.updated_at > stale_updated_at


def test_log_run_returns_a_run_bound_to_the_hunt() -> None:
    hunt = make_hunt()
    hunt.id = 42

    run = log_run(hunt, RunOutcome.inconclusive, notes="check again", duration_minutes=15)

    assert run.hunt_id == 42
    assert run.outcome == RunOutcome.inconclusive
    assert run.notes == "check again"
    assert run.duration_minutes == 15
