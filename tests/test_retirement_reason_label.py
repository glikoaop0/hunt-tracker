import pytest

from app.models import Hunt, HuntStatus, RetirementReason


def make_hunt(**overrides) -> Hunt:
    defaults = dict(title="Test hunt", hypothesis="Test hypothesis", status=HuntStatus.retired)
    defaults.update(overrides)
    return Hunt(**defaults)


@pytest.mark.parametrize(
    ("reason", "expected_label"),
    [
        (RetirementReason.converted_to_detection, "Converted to detection"),
        (RetirementReason.completed_one_off, "Completed one off"),
        (RetirementReason.hypothesis_rejected, "Hypothesis rejected"),
        (RetirementReason.missing_telemetry, "Missing telemetry"),
        (RetirementReason.superseded, "Superseded"),
        (RetirementReason.no_longer_relevant, "No longer relevant"),
    ],
)
def test_retirement_reason_enum_label_formats_value(
    reason: RetirementReason, expected_label: str
) -> None:
    assert reason.label == expected_label


def test_hunt_retirement_reason_label_delegates_to_enum_label_when_set() -> None:
    hunt = make_hunt(retirement_reason=RetirementReason.superseded)

    assert hunt.retirement_reason_label == "Superseded"


def test_hunt_retirement_reason_label_returns_no_reason_recorded_when_none() -> None:
    hunt = make_hunt(retirement_reason=None)

    assert hunt.retirement_reason_label == "No reason recorded"
