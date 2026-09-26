from datetime import UTC, datetime, timedelta

import pytest

from app.models import Hunt, HuntStatus, RunOutcome
from app.services import log_run


def make_hunt(**overrides) -> Hunt:
    defaults = dict(title="Test hunt", hypothesis="Test hypothesis", status=HuntStatus.active)
    defaults.update(overrides)
    return Hunt(**defaults)


def test_log_run_copies_hunt_query_into_snapshot() -> None:
    hunt = make_hunt(query="index=edr process_name=rundll32.exe")

    run = log_run(hunt, RunOutcome.no_findings)

    assert run.query_snapshot == "index=edr process_name=rundll32.exe"


def test_log_run_snapshot_preserves_multiline_content() -> None:
    query = "index=edr\n| where match(cmd, \"a\\\"b\")\n| stats count by host"
    hunt = make_hunt(query=query)

    run = log_run(hunt, RunOutcome.no_findings)

    assert run.query_snapshot == query


def test_editing_hunt_query_does_not_alter_previous_snapshot() -> None:
    hunt = make_hunt(query="index=edr first_version")
    first_run = log_run(hunt, RunOutcome.no_findings)

    hunt.query = "index=edr second_version"
    second_run = log_run(hunt, RunOutcome.findings)

    assert first_run.query_snapshot == "index=edr first_version"
    assert second_run.query_snapshot == "index=edr second_version"


def test_empty_hunt_query_gives_no_query_captured_label() -> None:
    hunt = make_hunt(query="")

    run = log_run(hunt, RunOutcome.no_findings)

    assert run.query_snapshot == ""
    assert run.query_snapshot_label == "No query captured"


def test_legacy_run_with_null_snapshot_uses_no_query_captured_label() -> None:
    hunt = make_hunt()
    run = log_run(hunt, RunOutcome.no_findings)
    run.query_snapshot = None  # simulate a row created before this feature existed

    assert run.query_snapshot_label == "No query captured"


def test_valid_search_window_persists() -> None:
    hunt = make_hunt()
    start = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 20, 0, tzinfo=UTC)

    run = log_run(hunt, RunOutcome.no_findings, search_from=start, search_to=end)

    assert run.search_from == start
    assert run.search_to == end
    assert run.has_search_window is True


def test_reversed_search_window_is_rejected() -> None:
    hunt = make_hunt()
    start = datetime(2026, 1, 2, tzinfo=UTC)
    end = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(ValueError):
        log_run(hunt, RunOutcome.no_findings, search_from=start, search_to=end)


def test_equal_search_from_and_to_is_accepted() -> None:
    hunt = make_hunt()
    moment = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    run = log_run(hunt, RunOutcome.no_findings, search_from=moment, search_to=moment)

    assert run.search_from == moment
    assert run.search_to == moment


@pytest.mark.parametrize(
    ("search_from", "search_to"),
    [
        (datetime(2026, 1, 1, tzinfo=UTC), None),
        (None, datetime(2026, 1, 1, tzinfo=UTC)),
    ],
)
def test_one_sided_search_window_is_rejected(search_from, search_to) -> None:
    hunt = make_hunt()

    with pytest.raises(ValueError):
        log_run(hunt, RunOutcome.no_findings, search_from=search_from, search_to=search_to)


def test_negative_result_count_is_rejected() -> None:
    hunt = make_hunt()

    with pytest.raises(ValueError):
        log_run(hunt, RunOutcome.no_findings, result_count=-1)


def test_zero_result_count_is_accepted() -> None:
    hunt = make_hunt()

    run = log_run(hunt, RunOutcome.no_findings, result_count=0)

    assert run.result_count == 0


def test_invalid_run_does_not_mutate_hunt_state() -> None:
    hunt = make_hunt(cadence_days=14, next_run=None)
    stale_updated_at = datetime(2020, 1, 1, tzinfo=UTC)
    hunt.updated_at = stale_updated_at

    with pytest.raises(ValueError):
        log_run(hunt, RunOutcome.no_findings, result_count=-5)

    assert hunt.next_run is None
    assert hunt.updated_at == stale_updated_at


def test_cadence_advancement_still_works_with_new_fields() -> None:
    hunt = make_hunt(cadence_days=14, next_run=None)

    run = log_run(hunt, RunOutcome.no_findings, result_count=3)

    assert hunt.next_run == run.ran_at.date() + timedelta(days=14)
