from datetime import UTC, date, datetime

import pytest

from app.models import Hunt, HuntStatus, RetirementReason
from app.services import update_hunt


def make_hunt(**overrides) -> Hunt:
    defaults = dict(
        title="Original title",
        hypothesis="Original hypothesis",
        status=HuntStatus.active,
        priority=3,
        data_sources="Splunk",
        attack_techniques="T1059",
        query="index=edr",
        notes="Some notes",
        cadence_days=14,
        next_run=date(2026, 1, 1),
    )
    defaults.update(overrides)
    return Hunt(**defaults)


def test_update_hunt_persists_all_editable_fields() -> None:
    hunt = make_hunt()

    update_hunt(
        hunt,
        title="New title",
        hypothesis="New hypothesis",
        priority=1,
        data_sources="Zeek",
        attack_techniques="T1218",
        query="index=dns",
        notes="Updated notes",
        cadence_days=30,
        next_run=date(2026, 2, 1),
    )

    assert hunt.title == "New title"
    assert hunt.hypothesis == "New hypothesis"
    assert hunt.priority == 1
    assert hunt.data_sources == "Zeek"
    assert hunt.attack_techniques == "T1218"
    assert hunt.query == "index=dns"
    assert hunt.notes == "Updated notes"
    assert hunt.cadence_days == 30
    assert hunt.next_run == date(2026, 2, 1)


def test_update_hunt_strips_title_and_hypothesis() -> None:
    hunt = make_hunt()

    update_hunt(hunt, title="  Padded title  ", hypothesis="  Padded hypothesis  ", priority=3)

    assert hunt.title == "Padded title"
    assert hunt.hypothesis == "Padded hypothesis"


def test_update_hunt_rejects_blank_title() -> None:
    hunt = make_hunt()
    with pytest.raises(ValueError):
        update_hunt(hunt, title="", hypothesis="Hypothesis", priority=3)


def test_update_hunt_rejects_whitespace_only_title() -> None:
    hunt = make_hunt()
    with pytest.raises(ValueError):
        update_hunt(hunt, title="   ", hypothesis="Hypothesis", priority=3)


def test_update_hunt_rejects_blank_hypothesis() -> None:
    hunt = make_hunt()
    with pytest.raises(ValueError):
        update_hunt(hunt, title="Title", hypothesis="", priority=3)


def test_update_hunt_rejects_whitespace_only_hypothesis() -> None:
    hunt = make_hunt()
    with pytest.raises(ValueError):
        update_hunt(hunt, title="Title", hypothesis="   ", priority=3)


@pytest.mark.parametrize("priority", [0, -1, 6, 100])
def test_update_hunt_rejects_out_of_range_priority(priority: int) -> None:
    hunt = make_hunt()
    with pytest.raises(ValueError):
        update_hunt(hunt, title="Title", hypothesis="Hypothesis", priority=priority)


@pytest.mark.parametrize("cadence_days", [0, -1, -30])
def test_update_hunt_rejects_non_positive_cadence(cadence_days: int) -> None:
    hunt = make_hunt()
    with pytest.raises(ValueError):
        update_hunt(hunt, title="Title", hypothesis="Hypothesis", priority=3, cadence_days=cadence_days)


def test_update_hunt_does_not_partially_apply_on_failure() -> None:
    hunt = make_hunt(title="Stays the same")

    with pytest.raises(ValueError):
        update_hunt(hunt, title="Stays the same", hypothesis="", priority=3, data_sources="Should not apply")

    assert hunt.title == "Stays the same"
    assert hunt.data_sources == "Splunk"


def test_update_hunt_clearing_cadence_also_clears_next_run() -> None:
    hunt = make_hunt(cadence_days=14, next_run=date(2026, 1, 1))

    update_hunt(hunt, title="Title", hypothesis="Hypothesis", priority=3, cadence_days=None, next_run=date(2026, 6, 1))

    assert hunt.cadence_days is None
    assert hunt.next_run is None


def test_update_hunt_setting_cadence_keeps_submitted_next_run() -> None:
    hunt = make_hunt(cadence_days=None, next_run=None)

    update_hunt(hunt, title="Title", hypothesis="Hypothesis", priority=3, cadence_days=14, next_run=date(2026, 3, 1))

    assert hunt.cadence_days == 14
    assert hunt.next_run == date(2026, 3, 1)


def test_update_hunt_optional_fields_can_be_cleared() -> None:
    hunt = make_hunt(data_sources="Splunk", attack_techniques="T1059", query="index=edr", notes="Some notes")

    update_hunt(hunt, title="Title", hypothesis="Hypothesis", priority=3)

    assert hunt.data_sources == ""
    assert hunt.attack_techniques == ""
    assert hunt.query == ""
    assert hunt.notes == ""


def test_update_hunt_preserves_created_at() -> None:
    hunt = make_hunt()
    original_created_at = hunt.created_at

    update_hunt(hunt, title="Title", hypothesis="Hypothesis", priority=3)

    assert hunt.created_at == original_created_at


def test_update_hunt_bumps_updated_at() -> None:
    hunt = make_hunt()
    hunt.updated_at = datetime(2020, 1, 1, tzinfo=UTC)

    update_hunt(hunt, title="Title", hypothesis="Hypothesis", priority=3)

    assert hunt.updated_at > datetime(2020, 1, 1, tzinfo=UTC)


def test_update_hunt_does_not_touch_status_or_retirement_fields() -> None:
    hunt = make_hunt(
        status=HuntStatus.retired,
        retirement_reason=RetirementReason.superseded,
        retirement_note="Old note",
        retired_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    update_hunt(hunt, title="Title", hypothesis="Hypothesis", priority=3)

    assert hunt.status == HuntStatus.retired
    assert hunt.retirement_reason == RetirementReason.superseded
    assert hunt.retirement_note == "Old note"
    assert hunt.retired_at == datetime(2025, 1, 1, tzinfo=UTC)


def test_update_hunt_preserves_multiline_query_with_special_characters() -> None:
    hunt = make_hunt()
    tricky_query = 'index=edr | where match(cmd, "a\\"b") AND x > 1\nstats count by host'

    update_hunt(hunt, title="Title", hypothesis="Hypothesis", priority=3, query=tricky_query)

    assert hunt.query == tricky_query
