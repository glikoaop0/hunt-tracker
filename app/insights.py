"""Server-computed aggregates for the Insights page.

Statistical definitions (also covered by tests in tests/test_insights.py):
- Active Hunts means status == HuntStatus.active.
- Overdue Hunts uses the existing Hunt.is_overdue rule (next_run set and in the past).
- "Runs in period" are Run rows whose ran_at falls within the selected date range
  (all-time if range is "all"), using the existing Run.ran_at timestamp.
- Converted to detection means retirement_reason == RetirementReason.converted_to_detection.
- Active exclusions means Exclusion.active is True.

Recent activity is deliberately independent of the selected range: it always
shows the most recent runs regardless of the date-range control, and is
labelled as such in the template.
"""

from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from app.models import Exclusion, Hunt, HuntStatus, RetirementReason, Run, RunOutcome

RANGE_DAYS = {"7": 7, "30": 30, "90": 90, "all": None}
DEFAULT_RANGE = "30"
RECENT_ACTIVITY_LIMIT = 10


def resolve_range(range_key: str | None) -> str:
    """Fall back safely to the default range for any unrecognised value."""
    if range_key in RANGE_DAYS:
        return range_key
    return DEFAULT_RANGE


def _range_cutoff(range_key: str) -> datetime | None:
    days = RANGE_DAYS[range_key]
    if days is None:
        return None
    # Naive UTC to match datetime values round-tripped from SQLite, which
    # drops tzinfo (Run.ran_at is stored via datetime.now(UTC) but loads back naive).
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)


def build_insights(session: Session, range_key: str | None) -> dict:
    range_key = resolve_range(range_key)
    cutoff = _range_cutoff(range_key)

    hunts = session.exec(select(Hunt)).all()
    runs = session.exec(select(Run)).all()
    exclusions = session.exec(select(Exclusion)).all()

    lifecycle_totals = {status: 0 for status in HuntStatus}
    for hunt in hunts:
        lifecycle_totals[hunt.status] += 1

    runs_in_period = [run for run in runs if cutoff is None or run.ran_at >= cutoff]

    outcome_totals = {outcome: 0 for outcome in RunOutcome}
    for run in runs_in_period:
        outcome_totals[run.outcome] += 1

    retirement_totals: dict[RetirementReason, int] = {}
    for hunt in hunts:
        if hunt.retirement_reason is not None:
            retirement_totals[hunt.retirement_reason] = retirement_totals.get(hunt.retirement_reason, 0) + 1
    ranked_retirements = sorted(retirement_totals.items(), key=lambda item: item[1], reverse=True)

    recent_runs = sorted(runs, key=lambda run: run.ran_at, reverse=True)[:RECENT_ACTIVITY_LIMIT]

    lifecycle_rows = [(status, lifecycle_totals[status]) for status in HuntStatus]
    outcome_rows = [(outcome, outcome_totals[outcome]) for outcome in RunOutcome]

    return {
        "range_key": range_key,
        "total_hunts": len(hunts),
        "active_hunts": lifecycle_totals[HuntStatus.active],
        "overdue_hunts": sum(1 for hunt in hunts if hunt.is_overdue),
        "runs_in_period_count": len(runs_in_period),
        "converted_to_detection": sum(
            1 for hunt in hunts if hunt.retirement_reason == RetirementReason.converted_to_detection
        ),
        "active_exclusions": sum(1 for exclusion in exclusions if exclusion.active),
        "lifecycle_rows": lifecycle_rows,
        "lifecycle_max": max((count for _, count in lifecycle_rows), default=0) or 1,
        "outcome_rows": outcome_rows,
        "outcome_max": max((count for _, count in outcome_rows), default=0) or 1,
        "retirement_rows": ranked_retirements,
        "retirement_max": max((count for _, count in ranked_retirements), default=0) or 1,
        "recent_runs": recent_runs,
    }
