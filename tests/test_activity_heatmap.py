from collections.abc import Iterator
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from app.activity import (
    InvalidDayError,
    build_activity_heatmap,
    build_day_detail,
    intensity_level,
    parse_day,
    period_start,
    plain_preview,
)
from app.db import get_session
from app.main import app
from app.models import Campaign, CampaignHunt, Hunt, HuntStatus, RetirementReason, Run, RunOutcome

TODAY = date(2026, 9, 20)  # a Sunday


def make_hunt(session: Session, title: str = "Hunt", status: HuntStatus = HuntStatus.active, **fields: object) -> Hunt:
    hunt = Hunt(title=title, hypothesis="h", status=status, **fields)
    session.add(hunt)
    session.commit()
    session.refresh(hunt)
    return hunt


def make_run(
    session: Session,
    hunt: Hunt,
    when: datetime,
    outcome: RunOutcome = RunOutcome.no_findings,
    notes: str = "",
    **fields: object,
) -> Run:
    run = Run(hunt_id=hunt.id, ran_at=when, outcome=outcome, notes=notes, **fields)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def cell_for(heatmap, day: date):
    for row in heatmap.rows:
        for cell in row:
            if cell is not None and cell.day == day:
                return cell
    return None


def real_cells(heatmap) -> list:
    return [cell for row in heatmap.rows for cell in row if cell is not None]


def column_of(heatmap, day: date) -> int:
    for row in heatmap.rows:
        for column, cell in enumerate(row):
            if cell is not None and cell.day == day:
                return column
    raise AssertionError(f"{day} not in grid")


# --- Empty data and zero-filled dates ---


def test_empty_database_yields_zero_filled_grid(session: Session) -> None:
    heatmap = build_activity_heatmap(session, TODAY)

    assert heatmap.total_runs == 0
    assert heatmap.distinct_hunts == 0
    assert heatmap.active_days == 0
    assert heatmap.runs_this_week == 0
    cells = real_cells(heatmap)
    assert len(cells) == (TODAY - date(2025, 10, 1)).days + 1
    assert all(cell.run_count == 0 and cell.level == 0 and not cell.has_findings for cell in cells)
    assert "no runs recorded" in cells[0].aria_label


def test_days_without_runs_are_present_between_active_days(session: Session) -> None:
    hunt = make_hunt(session)
    make_run(session, hunt, datetime(2026, 3, 1, 10))
    make_run(session, hunt, datetime(2026, 3, 5, 10))
    heatmap = build_activity_heatmap(session, TODAY)

    for day in (date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)):
        cell = cell_for(heatmap, day)
        assert cell is not None and cell.run_count == 0


# --- Intensity buckets ---


@pytest.mark.parametrize(
    ("count", "level"),
    [(0, 0), (1, 1), (2, 2), (3, 2), (4, 3), (5, 3), (6, 4), (7, 4), (50, 4)],
)
def test_intensity_bucket_boundaries(count: int, level: int) -> None:
    assert intensity_level(count) == level


def test_cell_level_follows_run_count(session: Session) -> None:
    hunt = make_hunt(session)
    for day, count in ((date(2026, 5, 1), 3), (date(2026, 5, 2), 4), (date(2026, 5, 3), 6)):
        for minute in range(count):
            make_run(session, hunt, datetime(day.year, day.month, day.day, 9, minute))
    heatmap = build_activity_heatmap(session, TODAY)

    assert cell_for(heatmap, date(2026, 5, 1)).level == 2
    assert cell_for(heatmap, date(2026, 5, 2)).level == 3
    assert cell_for(heatmap, date(2026, 5, 3)).level == 4


# --- Same hunt, findings, retired hunts ---


def test_several_runs_from_one_hunt_count_as_one_hunt(session: Session) -> None:
    hunt = make_hunt(session)
    for hour in (8, 12, 16):
        make_run(session, hunt, datetime(2026, 6, 10, hour))
    heatmap = build_activity_heatmap(session, TODAY)

    cell = cell_for(heatmap, date(2026, 6, 10))
    assert (cell.run_count, cell.hunt_count) == (3, 1)
    assert heatmap.total_runs == 3
    assert heatmap.distinct_hunts == 1
    assert heatmap.active_days == 1


def test_only_findings_outcome_sets_the_findings_marker(session: Session) -> None:
    hunt = make_hunt(session)
    make_run(session, hunt, datetime(2026, 6, 1, 9), RunOutcome.detection_opportunity)
    make_run(session, hunt, datetime(2026, 6, 2, 9), RunOutcome.inconclusive)
    make_run(session, hunt, datetime(2026, 6, 3, 9), RunOutcome.no_findings)
    make_run(session, hunt, datetime(2026, 6, 4, 9), RunOutcome.no_findings)
    make_run(session, hunt, datetime(2026, 6, 4, 10), RunOutcome.findings)
    make_run(session, hunt, datetime(2026, 6, 4, 11), RunOutcome.findings)
    heatmap = build_activity_heatmap(session, TODAY)

    for day in (date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)):
        assert not cell_for(heatmap, day).has_findings
    marked = cell_for(heatmap, date(2026, 6, 4))
    assert marked.has_findings
    assert marked.findings_count == 2
    assert marked.run_count == 3
    assert "2 marked Findings" in marked.aria_label


def test_runs_from_retired_hunts_are_included(session: Session) -> None:
    retired = make_hunt(
        session,
        "Old hunt",
        HuntStatus.retired,
        retirement_reason=RetirementReason.superseded,
        retired_at=datetime(2026, 7, 1),
    )
    make_run(session, retired, datetime(2026, 4, 2, 9), RunOutcome.findings)
    heatmap = build_activity_heatmap(session, TODAY)

    assert heatmap.total_runs == 1
    assert heatmap.distinct_hunts == 1
    assert cell_for(heatmap, date(2026, 4, 2)).findings_count == 1
    assert [run.hunt_title for run in build_day_detail(session, date(2026, 4, 2)).runs] == ["Old hunt"]


def test_campaign_membership_does_not_duplicate_runs(session: Session) -> None:
    hunt = make_hunt(session)
    make_run(session, hunt, datetime(2026, 8, 3, 9))
    for title in ("Campaign A", "Campaign B"):
        campaign = Campaign(title=title, objective="o")
        session.add(campaign)
        session.commit()
        session.refresh(campaign)
        session.add(CampaignHunt(campaign_id=campaign.id, hunt_id=hunt.id))
    session.commit()

    heatmap = build_activity_heatmap(session, TODAY)
    assert heatmap.total_runs == 1
    assert cell_for(heatmap, date(2026, 8, 3)).run_count == 1
    assert build_day_detail(session, date(2026, 8, 3)).run_count == 1


def test_search_window_is_never_used_for_the_activity_date(session: Session) -> None:
    hunt = make_hunt(session)
    make_run(
        session,
        hunt,
        datetime(2026, 9, 19, 12),
        search_from=datetime(2026, 1, 1),
        search_to=datetime(2026, 1, 31),
    )
    heatmap = build_activity_heatmap(session, TODAY)

    assert cell_for(heatmap, date(2026, 9, 19)).run_count == 1
    assert cell_for(heatmap, date(2026, 1, 15)).run_count == 0
    assert heatmap.active_days == 1


# --- Period boundaries, leap day, week alignment, midnight ---


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 9, 20), date(2025, 10, 1)),
        (date(2026, 1, 15), date(2025, 2, 1)),
        (date(2026, 12, 31), date(2026, 1, 1)),
        (date(2028, 2, 29), date(2027, 3, 1)),
        (date(2028, 6, 15), date(2027, 7, 1)),
    ],
)
def test_period_starts_on_first_of_month_eleven_months_back(today: date, expected: date) -> None:
    assert period_start(today) == expected


def test_period_boundaries_are_inclusive_of_start_and_today_only(session: Session) -> None:
    hunt = make_hunt(session)
    make_run(session, hunt, datetime(2025, 9, 30, 23, 59, 59, 999999))
    make_run(session, hunt, datetime(2025, 10, 1, 0, 0, 0))
    make_run(session, hunt, datetime(2026, 9, 20, 23, 59, 59, 999999))
    make_run(session, hunt, datetime(2026, 9, 21, 0, 0, 0))
    heatmap = build_activity_heatmap(session, TODAY)

    assert heatmap.total_runs == 2
    assert cell_for(heatmap, date(2025, 10, 1)).run_count == 1
    assert cell_for(heatmap, TODAY).run_count == 1
    assert cell_for(heatmap, date(2025, 9, 30)) is None
    assert cell_for(heatmap, date(2026, 9, 21)) is None


def test_timestamps_around_midnight_land_on_the_right_day(session: Session) -> None:
    hunt = make_hunt(session)
    make_run(session, hunt, datetime(2026, 9, 18, 23, 59, 59, 999999))
    make_run(session, hunt, datetime(2026, 9, 19, 0, 0, 0))
    make_run(session, hunt, datetime(2026, 9, 19, 23, 59, 59))
    make_run(session, hunt, datetime(2026, 9, 20, 0, 0, 0))
    heatmap = build_activity_heatmap(session, TODAY)

    assert cell_for(heatmap, date(2026, 9, 18)).run_count == 1
    assert cell_for(heatmap, date(2026, 9, 19)).run_count == 2
    assert cell_for(heatmap, date(2026, 9, 20)).run_count == 1
    assert build_day_detail(session, date(2026, 9, 19)).run_count == 2
    assert build_day_detail(session, date(2026, 9, 18)).run_count == 1


def test_leap_day_is_a_real_cell_on_a_tuesday(session: Session) -> None:
    hunt = make_hunt(session)
    make_run(session, hunt, datetime(2028, 2, 29, 12))
    heatmap = build_activity_heatmap(session, date(2028, 6, 15))

    cell = cell_for(heatmap, date(2028, 2, 29))
    assert cell is not None and cell.run_count == 1
    assert heatmap.rows[1][column_of(heatmap, date(2028, 2, 29))] is cell  # Tuesday row
    assert cell_for(heatmap, date(2028, 3, 1)) is not None
    assert len(real_cells(heatmap)) == (date(2028, 6, 15) - date(2027, 7, 1)).days + 1


def test_today_can_be_the_leap_day(session: Session) -> None:
    heatmap = build_activity_heatmap(session, date(2028, 2, 29))

    assert heatmap.start == date(2027, 3, 1)
    assert len(real_cells(heatmap)) == 366
    assert cell_for(heatmap, date(2028, 2, 29)) is not None


def test_week_alignment_monday_first_with_blank_padding(session: Session) -> None:
    heatmap = build_activity_heatmap(session, TODAY)

    assert len(heatmap.rows) == 7
    assert all(len(row) == heatmap.weeks for row in heatmap.rows)
    assert heatmap.weeks == 51
    # 2025-10-01 is a Wednesday: Mon/Tue of the first column fall outside the period.
    assert heatmap.rows[0][0] is None and heatmap.rows[1][0] is None
    assert heatmap.rows[2][0].day == date(2025, 10, 1)
    # Every real cell sits in the row matching its weekday.
    for weekday, row in enumerate(heatmap.rows):
        assert all(cell.day.weekday() == weekday for cell in row if cell is not None)
    # Today is a Sunday, so the final column is complete.
    assert all(row[-1] is not None for row in heatmap.rows)
    assert heatmap.rows[0][-1].day == date(2026, 9, 14)


def test_days_after_today_in_the_final_week_are_blank(session: Session) -> None:
    heatmap = build_activity_heatmap(session, date(2026, 9, 16))  # Wednesday

    last_column = [row[-1] for row in heatmap.rows]
    assert [cell.day if cell else None for cell in last_column] == [
        date(2026, 9, 14),
        date(2026, 9, 15),
        date(2026, 9, 16),
        None,
        None,
        None,
        None,
    ]


def test_month_labels_for_a_known_period(session: Session) -> None:
    heatmap = build_activity_heatmap(session, TODAY)

    assert [label.text for label in heatmap.month_labels] == [
        "Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
    ]
    columns = {label.text: label.column for label in heatmap.month_labels[:3]}
    assert columns == {"Oct": 0, "Nov": 5, "Dec": 9}  # Nov 1 is a Saturday, so it labels the next column


def test_month_labels_sit_over_columns_belonging_to_that_month(session: Session) -> None:
    for offset in range(0, 900, 7):
        today = date(2025, 1, 1) + timedelta(days=offset)
        heatmap = build_activity_heatmap(session, today)
        grid_start = heatmap.start - timedelta(days=heatmap.start.weekday())
        previous = -4
        for label in heatmap.month_labels:
            thursday = grid_start + timedelta(days=label.column * 7 + 3)
            assert thursday.strftime("%b") == label.text, (today, label)
            assert label.column - previous >= 4, (today, label)
            assert label.column < heatmap.weeks
            previous = label.column


# --- Summary metrics ---


def test_summary_counts(session: Session) -> None:
    first, second, retired = (
        make_hunt(session, "First"),
        make_hunt(session, "Second"),
        make_hunt(session, "Retired", HuntStatus.retired, retirement_reason=RetirementReason.no_longer_relevant),
    )
    make_run(session, first, datetime(2026, 2, 1, 9))
    make_run(session, first, datetime(2026, 2, 1, 10))
    make_run(session, second, datetime(2026, 2, 2, 9))
    make_run(session, retired, datetime(2026, 9, 14, 9))
    make_run(session, first, datetime(2026, 9, 20, 9))
    make_run(session, first, datetime(2025, 8, 1, 9))  # before the period
    heatmap = build_activity_heatmap(session, TODAY)

    assert heatmap.total_runs == 5
    assert heatmap.distinct_hunts == 3
    assert heatmap.active_days == 4
    assert heatmap.runs_this_week == 2


def test_runs_this_week_is_monday_through_today(session: Session) -> None:
    hunt = make_hunt(session)
    make_run(session, hunt, datetime(2026, 9, 13, 23, 59, 59))  # Sunday before
    make_run(session, hunt, datetime(2026, 9, 14, 0, 0, 0))  # Monday
    make_run(session, hunt, datetime(2026, 9, 16, 12))  # Wednesday (today)
    make_run(session, hunt, datetime(2026, 9, 17, 12))  # Thursday, after "today"
    heatmap = build_activity_heatmap(session, date(2026, 9, 16))

    assert heatmap.week_start == date(2026, 9, 14)
    assert heatmap.runs_this_week == 2
    assert heatmap.week_label == "Mon 14 Sep – today"


def test_heatmap_uses_a_single_bounded_query(session: Session) -> None:
    hunt = make_hunt(session)
    make_run(session, hunt, datetime(2026, 9, 1, 9))
    statements: list[str] = []

    def record(conn, cursor, statement, *args) -> None:
        statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        build_activity_heatmap(session, TODAY)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert len(statements) == 1
    assert "ran_at >=" in statements[0] and "ran_at <" in statements[0]


def test_building_the_heatmap_is_read_only(session: Session) -> None:
    hunt = make_hunt(session)
    make_run(session, hunt, datetime(2026, 9, 1, 9))
    before_updated = hunt.updated_at

    build_activity_heatmap(session, TODAY)
    build_day_detail(session, date(2026, 9, 1))
    session.expire_all()

    assert session.get(Hunt, hunt.id).updated_at == before_updated
    assert len(session.exec(select(Run)).all()) == 1


# --- Day selection ---


def test_day_detail_excludes_other_dates_and_orders_newest_first(session: Session) -> None:
    hunt = make_hunt(session, "Alpha")
    other = make_hunt(session, "Beta")
    early = make_run(session, hunt, datetime(2026, 9, 10, 8, 0, 0), notes="early")
    late = make_run(session, other, datetime(2026, 9, 10, 17, 30, 0), RunOutcome.findings)
    tie_low = make_run(session, hunt, datetime(2026, 9, 10, 12, 0, 0))
    tie_high = make_run(session, other, datetime(2026, 9, 10, 12, 0, 0))
    make_run(session, hunt, datetime(2026, 9, 9, 23, 59, 59))
    make_run(session, hunt, datetime(2026, 9, 11, 0, 0, 0))

    detail = build_day_detail(session, date(2026, 9, 10))

    assert [run.run_id for run in detail.runs] == [late.id, tie_high.id, tie_low.id, early.id]
    assert detail.run_count == 4
    assert detail.hunt_count == 2
    assert detail.findings_count == 1
    assert detail.runs[0].time_label == "17:30:00 UTC"
    assert detail.date_label == "Thursday, 10 September 2026"


def test_day_detail_for_a_zero_run_day_is_empty(session: Session) -> None:
    detail = build_day_detail(session, date(2026, 9, 10))

    assert detail.runs == []
    assert detail.run_count == 0
    assert "no runs recorded" in detail.announcement


# --- Notes preview ---


def test_plain_preview_flattens_markdown_and_truncates() -> None:
    notes = "# Heading\n- **bold** item with `code` and [a link](http://example.test/x)\n\n```\nfenced\n```\n"
    assert plain_preview(notes) == "Heading bold item with code and a link fenced"
    long = plain_preview("word " * 100)
    assert len(long) == 140 and long.endswith("…")
    assert plain_preview("") == ""


def test_plain_preview_keeps_underscores_in_identifiers() -> None:
    assert plain_preview("host svc_backup_01 flagged") == "host svc_backup_01 flagged"


# --- Date parameter parsing ---


@pytest.mark.parametrize(
    "raw",
    ["", "abc", "2026-13-01", "2026-02-30", "2026-9-20", "20260920", "2026-W38-7", " 2026-09-20", "2026-09-20 ",
     "２０２６-09-20", "2026-09-21", "2025-09-30", "0001-01-01", "9999-12-31"],
)
def test_parse_day_rejects_invalid_or_out_of_period_values(raw: str) -> None:
    with pytest.raises(InvalidDayError):
        parse_day(raw, TODAY)


def test_parse_day_accepts_period_edges() -> None:
    assert parse_day("2025-10-01", TODAY) == date(2025, 10, 1)
    assert parse_day("2026-09-20", TODAY) == TODAY
    assert parse_day("2028-02-29", date(2028, 6, 15)) == date(2028, 2, 29)


# --- Routes ---


@pytest.fixture()
def client(session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr("app.activity.utc_today", lambda: TODAY)

    def override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_insights_page_renders_heatmap_with_labels_and_preserves_existing_sections(
    client: TestClient, session: Session
) -> None:
    hunt = make_hunt(session, "Renders")
    make_run(session, hunt, datetime(2026, 9, 18, 9), RunOutcome.findings)

    response = client.get("/insights")

    assert response.status_code == 200
    text = response.text
    assert "Recorded hunting activity" in text
    assert "1 Oct 2025" in text and "20 Sep 2026" in text
    assert "Trailing 12 calendar months" in text
    assert "UTC" in text
    for bucket in (">0<", "2–3", "4–5", "6+"):
        assert bucket in text
    assert "Coral outline: at least one run marked Findings." in text
    assert "Recorded runs show activity, not investigation quality or defensive coverage." in text
    assert 'data-date="2026-09-18"' in text and "has-findings" in text
    assert "Runs this week" in text
    for existing in ("Hunt lifecycle distribution", "Run outcomes", "Retirement reasons", "Recent activity", "Last 30 days"):
        assert existing in text


def test_insights_page_has_exactly_one_tab_stop_in_the_grid(client: TestClient) -> None:
    text = client.get("/insights").text

    assert text.count('tabindex="0"') == 1
    assert text.count('class="heatmap-pad"') > 0
    assert 'data-date="2026-09-21"' not in text


def test_insights_page_renders_empty_state_without_runs(client: TestClient) -> None:
    response = client.get("/insights", params={"range": "all"})

    assert response.status_code == 200
    assert "No runs have been recorded in this period yet." in response.text
    assert "Select a day above" in response.text


def test_day_route_lists_only_the_selected_day(client: TestClient, session: Session) -> None:
    hunt = make_hunt(session, "Selected hunt")
    on_day = make_run(session, hunt, datetime(2026, 9, 10, 8, 15, 0), RunOutcome.findings, notes="**Beacon** seen")
    make_run(session, make_hunt(session, "Other day hunt"), datetime(2026, 9, 11, 8, 0, 0))

    response = client.get("/insights/activity/day", params={"date": "2026-09-10"})

    assert response.status_code == 200
    text = response.text
    assert "Thursday, 10 September 2026" in text
    assert "<strong>1</strong> run" in text
    assert "Selected hunt" in text and "Other day hunt" not in text
    assert "08:15:00 UTC" in text
    assert "Beacon seen" in text and "**" not in text
    assert f'href="/hunts/{hunt.id}"' in text
    assert f'href="/hunts/{hunt.id}#run-{on_day.id}"' in text


def test_day_route_zero_run_day_shows_empty_state(client: TestClient) -> None:
    response = client.get("/insights/activity/day", params={"date": "2026-09-10"})

    assert response.status_code == 200
    assert "No runs were recorded on this day." in response.text


def test_day_route_escapes_notes_and_titles(client: TestClient, session: Session) -> None:
    hunt = make_hunt(session, "<b>Bold title</b>")
    make_run(session, hunt, datetime(2026, 9, 10, 8), notes='<script>alert("x")</script> <img src=x onerror=alert(1)>')

    text = client.get("/insights/activity/day", params={"date": "2026-09-10"}).text

    assert "<script>" not in text
    assert "<img" not in text
    assert "<b>Bold" not in text
    assert "&lt;script&gt;" in text
    assert "&lt;b&gt;Bold title&lt;/b&gt;" in text


@pytest.mark.parametrize("value", ["", "nonsense", "2026-02-30", "2026-09-21", "2020-01-01", "20260920"])
def test_day_route_handles_invalid_dates_cleanly(client: TestClient, value: str) -> None:
    response = client.get("/insights/activity/day", params={"date": value})

    assert response.status_code == 400
    assert 'role="alert"' in response.text
    assert "Traceback" not in response.text


def test_day_route_handles_missing_date_cleanly(client: TestClient) -> None:
    response = client.get("/insights/activity/day")

    assert response.status_code == 400
    assert 'role="alert"' in response.text


def test_day_route_does_not_mutate_data(client: TestClient, session: Session) -> None:
    hunt = make_hunt(session)
    make_run(session, hunt, datetime(2026, 9, 10, 8))
    before = hunt.updated_at

    client.get("/insights/activity/day", params={"date": "2026-09-10"})
    client.get("/insights")
    session.expire_all()

    assert session.get(Hunt, hunt.id).updated_at == before
    assert len(session.exec(select(Run)).all()) == 1
