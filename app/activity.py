"""Server-computed read model for the Insights "Recorded hunting activity" heatmap.

Date and timezone conventions:
- Runs are placed on the UTC calendar date of Run.ran_at. The app stores naive
  UTC (datetime.now(UTC) loses its tzinfo on the SQLite round trip), so "today"
  and every day boundary here are UTC too.
- Run.ran_at is stamped when the run is logged; there is no separate
  user-entered execution time. Days therefore mean "when the run was recorded".
  Run.search_from / Run.search_to describe the data window searched and are
  never used for activity dates.
- The displayed period is the first day of the month 11 months before today's
  month, through today: 12 calendar months, independent of the Insights
  date-range selector.

Read-only: nothing here writes to the database.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlmodel import Session, col, select

from app.models import Hunt, Run, RunOutcome

TIMEZONE_LABEL = "UTC"
PERIOD_MONTHS = 12
NOTES_PREVIEW_LENGTH = 140

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WEEKDAY_AXIS_LABELS = ["Mon", "", "Wed", "", "Fri", "", ""]
MONTH_ABBREVIATIONS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

LEGEND_BUCKETS = [(0, "0"), (1, "1"), (2, "2–3"), (3, "4–5"), (4, "6+")]

_ISO_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class InvalidDayError(ValueError):
    pass


def utc_today() -> date:
    return datetime.now(UTC).date()


def intensity_level(run_count: int) -> int:
    if run_count <= 0:
        return 0
    if run_count == 1:
        return 1
    if run_count <= 3:
        return 2
    if run_count <= 5:
        return 3
    return 4


def period_start(today: date) -> date:
    months_since_year_zero = today.year * 12 + (today.month - 1) - (PERIOD_MONTHS - 1)
    year, month_index = divmod(months_since_year_zero, 12)
    return date(year, month_index + 1, 1)


def format_full_date(day: date) -> str:
    return f"{WEEKDAY_NAMES[day.weekday()]}, {day.day} {day:%B %Y}"


def format_short_date(day: date) -> str:
    return f"{day.day} {MONTH_ABBREVIATIONS[day.month - 1]} {day.year}"


def _plural(count: int, singular: str) -> str:
    return f"{count} {singular}" if count == 1 else f"{count} {singular}s"


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    lower = datetime.combine(day, time.min)
    return lower, lower + timedelta(days=1)


@dataclass(frozen=True)
class DayCell:
    day: date
    run_count: int
    hunt_count: int
    findings_count: int

    @property
    def level(self) -> int:
        return intensity_level(self.run_count)

    @property
    def has_findings(self) -> bool:
        return self.findings_count > 0

    @property
    def date_label(self) -> str:
        return format_full_date(self.day)

    @property
    def aria_label(self) -> str:
        if self.run_count == 0:
            return f"{self.date_label}: no runs recorded"
        text = (
            f"{self.date_label}: {_plural(self.run_count, 'run')} "
            f"across {_plural(self.hunt_count, 'hunt')}"
        )
        if self.findings_count:
            return f"{text}, {self.findings_count} marked Findings"
        return f"{text}, none marked Findings"


@dataclass(frozen=True)
class MonthLabel:
    column: int
    text: str


@dataclass(frozen=True)
class Heatmap:
    start: date
    end: date
    week_start: date
    weeks: int
    rows: list[list[DayCell | None]]
    month_labels: list[MonthLabel]
    total_runs: int
    distinct_hunts: int
    active_days: int
    runs_this_week: int

    @property
    def period_label(self) -> str:
        return f"{format_short_date(self.start)} – {format_short_date(self.end)}"

    @property
    def week_label(self) -> str:
        return f"Mon {self.week_start.day} {MONTH_ABBREVIATIONS[self.week_start.month - 1]} – today"

    @property
    def weekday_axis(self) -> list[str]:
        return WEEKDAY_AXIS_LABELS

    @property
    def weekday_names(self) -> list[str]:
        return WEEKDAY_NAMES

    @property
    def timezone_label(self) -> str:
        return TIMEZONE_LABEL

    @property
    def legend(self) -> list[tuple[int, str]]:
        return LEGEND_BUCKETS

    @property
    def focus_day(self) -> date:
        return self.end


def _month_labels(start: date, end: date, grid_start: date, weeks: int) -> list[MonthLabel]:
    """A month is labelled over the first column holding at least four of its
    days (the 1st falls Monday-Thursday), otherwise over the following column,
    so a month starting on a weekend isn't labelled above a mostly-previous-month column."""
    labels: list[MonthLabel] = []
    cursor = start
    while cursor <= end:
        column = (cursor - grid_start).days // 7
        if cursor.weekday() > 3:
            column += 1
        if column < weeks:
            labels.append(MonthLabel(column=column, text=MONTH_ABBREVIATIONS[cursor.month - 1]))
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return labels


def build_activity_heatmap(session: Session, today: date | None = None) -> Heatmap:
    today = today or utc_today()
    start = period_start(today)
    lower, _ = _day_bounds(start)
    _, upper = _day_bounds(today)

    runs_per_day: dict[date, int] = {}
    hunts_per_day: dict[date, set[int]] = {}
    findings_per_day: dict[date, int] = {}
    hunts_in_period: set[int] = set()

    statement = select(Run.hunt_id, Run.ran_at, Run.outcome).where(Run.ran_at >= lower, Run.ran_at < upper)
    for hunt_id, ran_at, outcome in session.exec(statement):
        day = ran_at.date()
        runs_per_day[day] = runs_per_day.get(day, 0) + 1
        hunts_per_day.setdefault(day, set()).add(hunt_id)
        hunts_in_period.add(hunt_id)
        if outcome == RunOutcome.findings:
            findings_per_day[day] = findings_per_day.get(day, 0) + 1

    grid_start = start - timedelta(days=start.weekday())
    grid_end = today + timedelta(days=6 - today.weekday())
    weeks = ((grid_end - grid_start).days + 1) // 7

    rows: list[list[DayCell | None]] = []
    for weekday in range(7):
        row: list[DayCell | None] = []
        for week in range(weeks):
            day = grid_start + timedelta(days=week * 7 + weekday)
            if day < start or day > today:
                row.append(None)
            else:
                row.append(
                    DayCell(
                        day=day,
                        run_count=runs_per_day.get(day, 0),
                        hunt_count=len(hunts_per_day.get(day, ())),
                        findings_count=findings_per_day.get(day, 0),
                    )
                )
        rows.append(row)

    week_start = today - timedelta(days=today.weekday())
    runs_this_week = sum(count for day, count in runs_per_day.items() if day >= week_start)

    return Heatmap(
        start=start,
        end=today,
        week_start=week_start,
        weeks=weeks,
        rows=rows,
        month_labels=_month_labels(start, today, grid_start, weeks),
        total_runs=sum(runs_per_day.values()),
        distinct_hunts=len(hunts_in_period),
        active_days=len(runs_per_day),
        runs_this_week=runs_this_week,
    )


def parse_day(raw: str | None, today: date | None = None) -> date:
    """Strict YYYY-MM-DD only (date.fromisoformat also accepts other ISO forms),
    restricted to the period the heatmap actually displays."""
    today = today or utc_today()
    if raw is None or not _ISO_DAY.fullmatch(raw):
        raise InvalidDayError("Choose a day from the calendar (expected a date like 2026-09-20).")
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        raise InvalidDayError(f"{raw} is not a real calendar date.") from None
    if day > today or day < period_start(today):
        raise InvalidDayError(
            f"{raw} is outside the displayed period ({format_short_date(period_start(today))} "
            f"– {format_short_date(today)})."
        )
    return day


_MD_LINE_PREFIX = re.compile(r"^\s{0,3}(?:#{1,6}|>+|[-*+]|[0-9]+[.)])\s+")
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MD_PAIRED = re.compile(r"(\*\*|~~|\*|`)(.+?)\1")


def plain_preview(notes: str, limit: int = NOTES_PREVIEW_LENGTH) -> str:
    """Flatten light markdown to one line of plain text. Deliberately does not
    strip or render HTML: the result is only ever emitted through Jinja's
    autoescaping, so markup in notes shows up as literal text."""
    lines = [_MD_LINE_PREFIX.sub("", line) for line in (notes or "").splitlines() if not line.strip().startswith("```")]
    text = " ".join(lines)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_PAIRED.sub(r"\2", text)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


@dataclass(frozen=True)
class DayRun:
    run_id: int
    hunt_id: int
    hunt_title: str
    recorded_at: datetime
    outcome: RunOutcome
    notes_preview: str

    @property
    def time_label(self) -> str:
        return f"{self.recorded_at:%H:%M:%S} {TIMEZONE_LABEL}"

    @property
    def outcome_label(self) -> str:
        return self.outcome.label


@dataclass(frozen=True)
class DayDetail:
    day: date
    runs: list[DayRun]

    @property
    def date_label(self) -> str:
        return format_full_date(self.day)

    @property
    def timezone_label(self) -> str:
        return TIMEZONE_LABEL

    @property
    def run_count(self) -> int:
        return len(self.runs)

    @property
    def hunt_count(self) -> int:
        return len({run.hunt_id for run in self.runs})

    @property
    def findings_count(self) -> int:
        return sum(1 for run in self.runs if run.outcome == RunOutcome.findings)

    @property
    def announcement(self) -> str:
        if not self.runs:
            return f"{self.date_label}: no runs recorded."
        return (
            f"{self.date_label}: {_plural(self.run_count, 'run')} across "
            f"{_plural(self.hunt_count, 'hunt')}, {self.findings_count} marked Findings."
        )


def build_day_detail(session: Session, day: date) -> DayDetail:
    lower, upper = _day_bounds(day)
    statement = (
        select(Run.id, Run.hunt_id, Hunt.title, Run.ran_at, Run.outcome, Run.notes)
        .join(Hunt, Run.hunt_id == Hunt.id)
        .where(Run.ran_at >= lower, Run.ran_at < upper)
        .order_by(col(Run.ran_at).desc(), col(Run.id).desc())
    )
    runs = [
        DayRun(
            run_id=run_id,
            hunt_id=hunt_id,
            hunt_title=title,
            recorded_at=ran_at,
            outcome=outcome,
            notes_preview=plain_preview(notes),
        )
        for run_id, hunt_id, title, ran_at, outcome, notes in session.exec(statement)
    ]
    return DayDetail(day=day, runs=runs)
