from datetime import UTC, datetime

import pytest

from app.models import Hunt, HuntStatus, RetirementReason
from app.services import move_hunt, reactivate_hunt, retire_hunt


def make_hunt(**overrides) -> Hunt:
    defaults = dict(title="Test hunt", hypothesis="Test hypothesis", status=HuntStatus.active)
    defaults.update(overrides)
    return Hunt(**defaults)


def test_retire_hunt_sets_status_reason_note_and_retired_at() -> None:
    hunt = make_hunt()

    retire_hunt(hunt, RetirementReason.converted_to_detection, note="Shipped as a detection.")

    assert hunt.status == HuntStatus.retired
    assert hunt.retirement_reason == RetirementReason.converted_to_detection
    assert hunt.retirement_note == "Shipped as a detection."
    assert hunt.retired_at is not None


def test_retire_hunt_accepts_blank_note() -> None:
    hunt = make_hunt()

    retire_hunt(hunt, RetirementReason.no_longer_relevant)

    assert hunt.status == HuntStatus.retired
    assert hunt.retirement_note == ""


def test_retire_hunt_rejects_missing_reason() -> None:
    hunt = make_hunt()

    with pytest.raises(ValueError):
        retire_hunt(hunt, None)


def test_retire_hunt_rejects_invalid_reason() -> None:
    hunt = make_hunt()

    with pytest.raises(ValueError):
        retire_hunt(hunt, "not_a_real_reason")


def test_retire_hunt_replaces_previous_retirement_data() -> None:
    hunt = make_hunt()
    retire_hunt(hunt, RetirementReason.missing_telemetry, note="First reason.")
    first_retired_at = hunt.retired_at

    retire_hunt(hunt, RetirementReason.superseded, note="Second reason.")

    assert hunt.retirement_reason == RetirementReason.superseded
    assert hunt.retirement_note == "Second reason."
    assert hunt.retired_at is not None
    assert hunt.retired_at >= first_retired_at


def test_move_hunt_rejects_retired_status() -> None:
    hunt = make_hunt(status=HuntStatus.active)

    with pytest.raises(ValueError):
        move_hunt(hunt, HuntStatus.retired)

    assert hunt.status == HuntStatus.active


def test_move_hunt_same_status_is_noop() -> None:
    hunt = make_hunt(status=HuntStatus.active)
    stale_updated_at = datetime(2020, 1, 1, tzinfo=UTC)
    hunt.updated_at = stale_updated_at

    move_hunt(hunt, HuntStatus.active)

    assert hunt.status == HuntStatus.active
    assert hunt.updated_at == stale_updated_at


def test_move_hunt_allows_idea_scoped_active_transitions() -> None:
    hunt = make_hunt(status=HuntStatus.idea)

    move_hunt(hunt, HuntStatus.scoped)
    assert hunt.status == HuntStatus.scoped

    move_hunt(hunt, HuntStatus.active)
    assert hunt.status == HuntStatus.active


def test_reactivation_preserves_previous_retirement_fields() -> None:
    hunt = make_hunt()
    retire_hunt(hunt, RetirementReason.hypothesis_rejected, note="Mostly noise.")
    retired_reason, retired_note, retired_at = (
        hunt.retirement_reason,
        hunt.retirement_note,
        hunt.retired_at,
    )

    move_hunt(hunt, HuntStatus.active)

    assert hunt.status == HuntStatus.active
    assert hunt.retirement_reason == retired_reason
    assert hunt.retirement_note == retired_note
    assert hunt.retired_at == retired_at


def test_reactivate_hunt_moves_to_active_and_keeps_retirement_history() -> None:
    hunt = make_hunt()
    retire_hunt(hunt, RetirementReason.hypothesis_rejected, note="Too noisy.")
    retired_at = hunt.retired_at

    reactivate_hunt(hunt)

    assert hunt.status == HuntStatus.active
    assert hunt.retirement_reason == RetirementReason.hypothesis_rejected
    assert hunt.retirement_note == "Too noisy."
    assert hunt.retired_at == retired_at


def test_reactivate_hunt_rejects_hunt_that_is_not_retired() -> None:
    hunt = make_hunt(status=HuntStatus.scoped)

    with pytest.raises(ValueError):
        reactivate_hunt(hunt)

    assert hunt.status == HuntStatus.scoped
