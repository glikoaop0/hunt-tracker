from datetime import UTC, date, datetime, timedelta

from sqlmodel import Session

from app.models import Campaign, CampaignHunt, CampaignStatus, Hunt, HuntStatus, RetirementReason, Run, RunOutcome

DELETE_CONFIRMATION_WORD = "DELETE"


def log_run(
    hunt: Hunt,
    outcome: RunOutcome,
    notes: str = "",
    duration_minutes: int | None = None,
    search_from: datetime | None = None,
    search_to: datetime | None = None,
    result_count: int | None = None,
) -> Run:
    if (search_from is None) != (search_to is None):
        raise ValueError("Provide both a search-from and search-to time, or leave both blank.")
    if search_from is not None and search_to is not None and search_from > search_to:
        raise ValueError("Search from must be earlier than or equal to search to.")
    if result_count is not None and result_count < 0:
        raise ValueError("Result count must be zero or greater.")

    run = Run(
        hunt_id=hunt.id,
        outcome=outcome,
        notes=notes,
        duration_minutes=duration_minutes,
        query_snapshot=hunt.query,
        search_from=search_from,
        search_to=search_to,
        result_count=result_count,
    )

    if hunt.cadence_days is not None:
        base = hunt.next_run if hunt.next_run is not None else run.ran_at.date()
        hunt.next_run = base + timedelta(days=hunt.cadence_days)

    hunt.updated_at = datetime.now(UTC)
    return run


def move_hunt(hunt: Hunt, status: HuntStatus) -> None:
    if status == HuntStatus.retired:
        raise ValueError("Retiring a hunt requires a reason; use retire_hunt() instead.")
    if hunt.status == status:
        return
    hunt.status = status
    hunt.updated_at = datetime.now(UTC)


def retire_hunt(hunt: Hunt, reason: RetirementReason, note: str = "") -> None:
    if not isinstance(reason, RetirementReason):
        raise ValueError("A valid retirement reason is required.")
    hunt.status = HuntStatus.retired
    hunt.retirement_reason = reason
    hunt.retirement_note = note
    hunt.retired_at = datetime.now(UTC)
    hunt.updated_at = hunt.retired_at


def reactivate_hunt(hunt: Hunt) -> None:
    if hunt.status != HuntStatus.retired:
        raise ValueError("Only a retired hunt can be reactivated.")
    move_hunt(hunt, HuntStatus.active)


def create_hunt(title: str, hypothesis: str, priority: int) -> Hunt:
    title = title.strip()
    hypothesis = hypothesis.strip()
    if not title:
        raise ValueError("Title is required.")
    if not hypothesis:
        raise ValueError("Hypothesis is required.")
    if not 1 <= priority <= 5:
        raise ValueError("Priority must be between 1 and 5.")
    return Hunt(title=title, hypothesis=hypothesis, priority=priority)


def update_hunt(
    hunt: Hunt,
    *,
    title: str,
    hypothesis: str,
    priority: int,
    data_sources: str = "",
    attack_techniques: str = "",
    query: str = "",
    notes: str = "",
    cadence_days: int | None = None,
    next_run: date | None = None,
) -> None:
    title = title.strip()
    hypothesis = hypothesis.strip()
    if not title:
        raise ValueError("Title is required.")
    if not hypothesis:
        raise ValueError("Hypothesis is required.")
    if not 1 <= priority <= 5:
        raise ValueError("Priority must be between 1 and 5.")
    if cadence_days is not None and cadence_days <= 0:
        raise ValueError("Cadence must be a positive number of days.")

    hunt.title = title
    hunt.hypothesis = hypothesis
    hunt.priority = priority
    hunt.data_sources = data_sources.strip()
    hunt.attack_techniques = attack_techniques.strip()
    hunt.query = query.strip()
    hunt.notes = notes.strip()
    hunt.cadence_days = cadence_days
    hunt.next_run = next_run if cadence_days is not None else None
    hunt.updated_at = datetime.now(UTC)


def create_campaign(
    title: str,
    objective: str,
    scope: str = "",
    notes: str = "",
    conclusion: str = "",
) -> Campaign:
    title = title.strip()
    objective = objective.strip()
    if not title:
        raise ValueError("Title is required.")
    if not objective:
        raise ValueError("Objective is required.")
    return Campaign(
        title=title,
        objective=objective,
        scope=scope.strip(),
        notes=notes.strip(),
        conclusion=conclusion.strip(),
    )


def update_campaign(
    campaign: Campaign,
    *,
    title: str,
    objective: str,
    scope: str = "",
    notes: str = "",
    conclusion: str = "",
) -> None:
    title = title.strip()
    objective = objective.strip()
    if not title:
        raise ValueError("Title is required.")
    if not objective:
        raise ValueError("Objective is required.")

    campaign.title = title
    campaign.objective = objective
    campaign.scope = scope.strip()
    campaign.notes = notes.strip()
    campaign.conclusion = conclusion.strip()
    campaign.updated_at = datetime.now(UTC)


def update_campaign_conclusion(campaign: Campaign, conclusion: str) -> None:
    campaign.conclusion = conclusion.strip()
    campaign.updated_at = datetime.now(UTC)


def set_campaign_status(campaign: Campaign, status: CampaignStatus) -> None:
    if not isinstance(status, CampaignStatus):
        raise ValueError("A valid campaign status is required.")
    campaign.status = status
    campaign.updated_at = datetime.now(UTC)


def link_hunt_to_campaign(campaign: Campaign, hunt: Hunt) -> CampaignHunt:
    if any(link.hunt_id == hunt.id for link in campaign.memberships):
        raise ValueError(f'"{hunt.title}" is already linked to this campaign.')
    # Set both the FK ints (so the row is complete without a flush) and the
    # relationship objects (so campaign.memberships / hunt.campaign_links are
    # correct immediately via back_populates, without a session round-trip).
    link = CampaignHunt(campaign_id=campaign.id, hunt_id=hunt.id)
    link.campaign = campaign
    link.hunt = hunt
    campaign.updated_at = datetime.now(UTC)
    return link


def unlink_hunt_from_campaign(campaign: Campaign, link: CampaignHunt) -> None:
    """Validates the membership belongs to this campaign and stamps
    updated_at. The caller is responsible for session.delete(link) --
    removing from campaign.memberships here would not by itself delete the
    row (no delete-orphan cascade), so keeping deletion explicit in the
    route avoids that trap."""
    if link.campaign_id != campaign.id:
        raise ValueError("That membership does not belong to this campaign.")
    campaign.updated_at = datetime.now(UTC)


def delete_hunt(session: Session, hunt: Hunt, confirmation: str) -> None:
    if confirmation.strip().upper() != DELETE_CONFIRMATION_WORD:
        raise ValueError(f'Type {DELETE_CONFIRMATION_WORD} to confirm. Nothing was deleted.')

    # No ON DELETE cascades exist in the schema, so children go first; exclusions
    # before runs because an exclusion can point at the run that produced it.
    for exclusion in list(hunt.exclusions):
        session.delete(exclusion)
    for run in list(hunt.runs):
        session.delete(run)
    now = datetime.now(UTC)
    for link in list(hunt.campaign_links):
        link.campaign.updated_at = now
        session.add(link.campaign)
        session.delete(link)
    session.flush()
    # The in-memory collections still hold the deleted children; reload them
    # (now empty) so deleting the hunt doesn't try to orphan rows that are gone.
    session.expire(hunt, ["exclusions", "runs", "campaign_links"])
    session.delete(hunt)
