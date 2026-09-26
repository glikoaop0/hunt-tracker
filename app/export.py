"""Export and backup helpers.

The JSON export is a snapshot format for backup and future reference — not
yet a guaranteed stable public API. Its shape may change if the data model
changes; `export_schema_version` exists precisely so a future consumer can
detect that.
"""

import os
import re
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session, select

from app.db import DATABASE_URL, DB_PATH
from app.models import Campaign, CampaignHunt, Exclusion, Hunt, Run

EXPORT_SCHEMA_VERSION = 1


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _run_to_dict(run: Run) -> dict:
    return {
        "id": run.id,
        "hunt_id": run.hunt_id,
        "ran_at": _iso(run.ran_at),
        "outcome": run.outcome.value,
        "notes": run.notes,
        "duration_minutes": run.duration_minutes,
        "query_snapshot": run.query_snapshot,
        "search_from": _iso(run.search_from),
        "search_to": _iso(run.search_to),
        "result_count": run.result_count,
    }


def _exclusion_to_dict(exclusion: Exclusion) -> dict:
    return {
        "id": exclusion.id,
        "hunt_id": exclusion.hunt_id,
        "run_id": exclusion.run_id,
        "value": exclusion.value,
        "reason": exclusion.reason,
        "created_at": _iso(exclusion.created_at),
        "active": exclusion.active,
    }


def _hunt_to_dict(hunt: Hunt) -> dict:
    return {
        "id": hunt.id,
        "title": hunt.title,
        "hypothesis": hunt.hypothesis,
        "query": hunt.query,
        "status": hunt.status.value,
        "priority": hunt.priority,
        "data_sources": hunt.data_sources,
        "attack_techniques": hunt.attack_techniques,
        "notes": hunt.notes,
        "cadence_days": hunt.cadence_days,
        "next_run": _iso(hunt.next_run),
        "retirement_reason": hunt.retirement_reason.value if hunt.retirement_reason else None,
        "retirement_note": hunt.retirement_note,
        "retired_at": _iso(hunt.retired_at),
        "created_at": _iso(hunt.created_at),
        "updated_at": _iso(hunt.updated_at),
        "runs": [_run_to_dict(run) for run in hunt.runs_by_recency],
        "exclusions": [_exclusion_to_dict(exclusion) for exclusion in hunt.exclusions],
    }


def _membership_to_dict(link: CampaignHunt) -> dict:
    return {
        "id": link.id,
        "hunt_id": link.hunt_id,
        "created_at": _iso(link.created_at),
    }


def _campaign_to_dict(campaign: Campaign) -> dict:
    return {
        "id": campaign.id,
        "title": campaign.title,
        "objective": campaign.objective,
        "scope": campaign.scope,
        "notes": campaign.notes,
        "conclusion": campaign.conclusion,
        "status": campaign.status.value,
        "created_at": _iso(campaign.created_at),
        "updated_at": _iso(campaign.updated_at),
        "memberships": [_membership_to_dict(link) for link in campaign.memberships],
    }


def build_json_export(session: Session) -> dict:
    hunts = session.exec(select(Hunt)).all()
    campaigns = session.exec(select(Campaign)).all()
    return {
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "application": "Hunt Tracker",
        "note": (
            "This is a point-in-time export for backup and reference. "
            "It is not yet a guaranteed stable public API."
        ),
        "hunts": [_hunt_to_dict(hunt) for hunt in hunts],
        "campaigns": [_campaign_to_dict(campaign) for campaign in campaigns],
    }


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "hunt"


def markdown_export_filename(hunt: Hunt) -> str:
    return f"{hunt.id}-{_slugify(hunt.title)}.md"


def _fenced_code_block(content: str) -> str:
    content = content or ""
    longest_run = 0
    current = 0
    for char in content:
        if char == "`":
            current += 1
            longest_run = max(longest_run, current)
        else:
            current = 0
    fence = "`" * max(3, longest_run + 1)
    return f"{fence}\n{content}\n{fence}"


def _single_line(text: str) -> str:
    return (text or "").replace("\n", " ").strip()


def build_hunt_markdown(hunt: Hunt) -> str:
    lines: list[str] = []
    lines.append(f"# {_single_line(hunt.title)}")
    lines.append("")
    lines.append(f"**Status:** {hunt.status.value.capitalize()}  **Priority:** {hunt.priority}")
    lines.append("")
    lines.append("## Hypothesis")
    lines.append("")
    lines.append(hunt.hypothesis or "—")
    lines.append("")
    lines.append("## Data sources")
    lines.append("")
    lines.append(_single_line(hunt.data_sources) or "—")
    lines.append("")
    lines.append("## ATT&CK techniques")
    lines.append("")
    lines.append(_single_line(hunt.attack_techniques) or "—")
    lines.append("")
    lines.append("## Current query")
    lines.append("")
    lines.append(_fenced_code_block(hunt.query) if hunt.query else "_No query recorded._")
    lines.append("")
    lines.append("## Scheduling")
    lines.append("")
    if hunt.cadence_days:
        lines.append(f"Cadence: every {hunt.cadence_days} days")
        lines.append("")
        lines.append(f"Next run: {hunt.next_run.isoformat() if hunt.next_run else '—'}")
    else:
        lines.append("One-off hunt (no cadence).")
    lines.append("")
    if hunt.notes:
        lines.append("## Notes")
        lines.append("")
        lines.append(hunt.notes)
        lines.append("")
    lines.append("## Run history")
    lines.append("")
    runs = hunt.runs_by_recency
    if runs:
        for run in runs:
            lines.append(f"### {run.ran_at.date().isoformat()} — {run.outcome_label}")
            lines.append("")
            if run.duration_minutes is not None:
                lines.append(f"- Duration: {run.duration_minutes} min")
            if run.has_search_window:
                lines.append(
                    f"- Search window: {run.search_from.isoformat()} → {run.search_to.isoformat()}"
                )
            if run.result_count is not None:
                lines.append(f"- Result count: {run.result_count}")
            lines.append("")
            if run.notes:
                lines.append(run.notes)
                lines.append("")
            lines.append("Query snapshot:")
            lines.append("")
            lines.append(
                _fenced_code_block(run.query_snapshot) if run.query_snapshot else "_No query captured._"
            )
            lines.append("")
    else:
        lines.append("_No runs logged yet._")
        lines.append("")
    lines.append("## Exclusions")
    lines.append("")
    if hunt.exclusions:
        for exclusion in hunt.exclusions:
            state = "active" if exclusion.active else "inactive"
            lines.append(
                f"- {_single_line(exclusion.value)} ({state}) — {_single_line(exclusion.reason)} "
                f"_(added {exclusion.created_at.date().isoformat()})_"
            )
        lines.append("")
    else:
        lines.append("_No exclusions recorded._")
        lines.append("")
    if hunt.was_ever_retired:
        lines.append("## Retirement")
        lines.append("")
        lines.append(f"Reason: {hunt.retirement_reason_label}")
        if hunt.retired_at:
            lines.append("")
            lines.append(f"Retired at: {hunt.retired_at.isoformat()}")
        if hunt.retirement_note:
            lines.append("")
            lines.append(hunt.retirement_note)
        lines.append("")
    lines.append("## Metadata")
    lines.append("")
    lines.append(f"- Created: {hunt.created_at.isoformat()}")
    lines.append(f"- Updated: {hunt.updated_at.isoformat()}")
    lines.append("")
    return "\n".join(lines)


def create_sqlite_backup() -> Path:
    """Create a consistent point-in-time copy of hunts.db using SQLite's own
    backup API (not a raw file copy), and return the path to a temporary file
    the caller must delete after use. Read-only against the live database."""
    if not DATABASE_URL.startswith("sqlite"):
        raise RuntimeError("Database backup is only supported for a SQLite database.")
    if not DB_PATH.exists():
        raise RuntimeError("No database file exists yet.")

    fd, tmp_name = tempfile.mkstemp(suffix=".db", prefix="hunts-backup-")
    os.close(fd)
    tmp_path = Path(tmp_name)

    source = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        dest = sqlite3.connect(tmp_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()

    return tmp_path
