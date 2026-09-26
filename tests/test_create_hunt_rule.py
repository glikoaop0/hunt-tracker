import pytest

from app.models import HuntStatus
from app.services import create_hunt


def test_create_hunt_strips_whitespace_from_title_and_hypothesis() -> None:
    hunt = create_hunt("  Rundll32 abuse  ", "  Attackers abuse rundll32.  ", 3)

    assert hunt.title == "Rundll32 abuse"
    assert hunt.hypothesis == "Attackers abuse rundll32."


def test_create_hunt_starts_in_idea() -> None:
    hunt = create_hunt("Title", "Hypothesis", 3)

    assert hunt.status == HuntStatus.idea


def test_create_hunt_rejects_blank_title() -> None:
    with pytest.raises(ValueError):
        create_hunt("", "Hypothesis", 3)


def test_create_hunt_rejects_whitespace_only_title() -> None:
    with pytest.raises(ValueError):
        create_hunt("   ", "Hypothesis", 3)


def test_create_hunt_rejects_blank_hypothesis() -> None:
    with pytest.raises(ValueError):
        create_hunt("Title", "", 3)


def test_create_hunt_rejects_whitespace_only_hypothesis() -> None:
    with pytest.raises(ValueError):
        create_hunt("Title", "   ", 3)


@pytest.mark.parametrize("priority", [0, -1, 6, 100])
def test_create_hunt_rejects_out_of_range_priority(priority: int) -> None:
    with pytest.raises(ValueError):
        create_hunt("Title", "Hypothesis", priority)


@pytest.mark.parametrize("priority", [1, 2, 3, 4, 5])
def test_create_hunt_accepts_every_valid_priority(priority: int) -> None:
    hunt = create_hunt("Title", "Hypothesis", priority)

    assert hunt.priority == priority


def test_create_hunt_leaves_optional_fields_at_model_defaults() -> None:
    hunt = create_hunt("Title", "Hypothesis", 3)

    assert hunt.query == ""
    assert hunt.data_sources == ""
    assert hunt.attack_techniques == ""
    assert hunt.notes == ""
    assert hunt.cadence_days is None
    assert hunt.next_run is None
    assert hunt.retirement_reason is None


def test_create_hunt_sets_server_generated_timestamps() -> None:
    hunt = create_hunt("Title", "Hypothesis", 3)

    assert hunt.created_at is not None
    assert hunt.updated_at is not None
