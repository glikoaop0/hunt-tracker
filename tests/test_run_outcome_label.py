import pytest

from app.models import Run, RunOutcome


@pytest.mark.parametrize(
    ("outcome", "expected_label"),
    [
        (RunOutcome.no_findings, "No findings"),
        (RunOutcome.findings, "Findings"),
        (RunOutcome.inconclusive, "Inconclusive"),
        (RunOutcome.detection_opportunity, "Detection opportunity"),
    ],
)
def test_outcome_label_formats_enum_value(outcome: RunOutcome, expected_label: str) -> None:
    run = Run(hunt_id=1, outcome=outcome)

    assert run.outcome_label == expected_label
