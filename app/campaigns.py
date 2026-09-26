"""Server-computed read models for the Campaigns list and detail pages.

Kept separate from services.py (mutations) the same way insights.py is kept
separate from the hunt business rules -- this module only reads and shapes
data, it never writes.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, select

from app.models import Campaign, CampaignHunt, Hunt, HuntStatus, Run, RunOutcome

ACTIVITY_PREVIEW_LENGTH = 140


@dataclass
class CampaignListRow:
    campaign: Campaign
    objective_preview: str
    linked_hunt_count: int
    run_count: int
    status_distribution: list[tuple[HuntStatus, int]]
    last_activity: datetime | None


@dataclass
class HuntCard:
    hunt: Hunt
    link: CampaignHunt
    latest_run: Run | None


@dataclass
class CampaignOverview:
    total_linked_hunts: int
    active_hunts: int
    hunts_with_findings: int
    status_distribution: list[tuple[HuntStatus, int]]
    hunt_cards: list[HuntCard]
    last_activity: datetime | None


@dataclass
class ActivityEntry:
    run: Run
    hunt: Hunt


def _preview(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def last_activity_for(campaign: Campaign) -> datetime | None:
    """Last activity is the most recent of: the campaign record's own
    updated_at (title/objective/notes/conclusion/status/membership edits) and
    any run logged against a currently linked hunt. It intentionally ignores
    hunts once they're unlinked -- activity tracks the campaign's current
    membership, not its full history."""
    latest = campaign.updated_at
    for hunt in campaign.hunts:
        for run in hunt.runs:
            if latest is None or run.ran_at > latest:
                latest = run.ran_at
    return latest


def _status_distribution(hunts: list[Hunt]) -> list[tuple[HuntStatus, int]]:
    counts = {status: 0 for status in HuntStatus}
    for hunt in hunts:
        counts[hunt.status] += 1
    return [(status, counts[status]) for status in HuntStatus]


def build_campaign_list(session: Session) -> list[CampaignListRow]:
    campaigns = session.exec(select(Campaign)).all()
    rows = []
    for campaign in campaigns:
        hunts = campaign.hunts
        rows.append(
            CampaignListRow(
                campaign=campaign,
                objective_preview=_preview(campaign.objective),
                linked_hunt_count=len(hunts),
                run_count=sum(len(hunt.runs) for hunt in hunts),
                status_distribution=_status_distribution(hunts),
                last_activity=last_activity_for(campaign),
            )
        )
    rows.sort(key=lambda row: row.last_activity or row.campaign.created_at, reverse=True)
    return rows


def build_campaign_overview(campaign: Campaign) -> CampaignOverview:
    hunts = campaign.hunts
    links_by_hunt_id = {link.hunt_id: link for link in campaign.memberships}

    active_hunts = sum(1 for hunt in hunts if hunt.status == HuntStatus.active)
    hunts_with_findings = sum(
        1 for hunt in hunts if any(run.outcome == RunOutcome.findings for run in hunt.runs)
    )

    status_distribution = _status_distribution(hunts)

    hunt_cards = [
        HuntCard(
            hunt=hunt,
            link=links_by_hunt_id[hunt.id],
            latest_run=hunt.runs_by_recency[0] if hunt.runs_by_recency else None,
        )
        for hunt in hunts
    ]

    return CampaignOverview(
        total_linked_hunts=len(hunts),
        active_hunts=active_hunts,
        hunts_with_findings=hunts_with_findings,
        status_distribution=status_distribution,
        hunt_cards=hunt_cards,
        last_activity=last_activity_for(campaign),
    )


def build_campaign_activity(campaign: Campaign) -> list[ActivityEntry]:
    entries = [
        ActivityEntry(run=run, hunt=hunt)
        for hunt in campaign.hunts
        for run in hunt.runs
    ]
    entries.sort(key=lambda entry: entry.run.ran_at, reverse=True)
    return entries


def note_preview(notes: str) -> str:
    return _preview(notes, ACTIVITY_PREVIEW_LENGTH)
