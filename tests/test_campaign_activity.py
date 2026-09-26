from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.campaigns import build_campaign_activity, build_campaign_overview
from app.models import Campaign, CampaignHunt, Hunt, HuntStatus, Run, RunOutcome


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _days_ago(n: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=n)


def test_activity_is_newest_first(session: Session) -> None:
    campaign = Campaign(title="Campaign", objective="Objective")
    hunt = Hunt(title="Hunt", hypothesis="Hypothesis", status=HuntStatus.active)
    session.add_all([campaign, hunt])
    session.commit()
    session.refresh(campaign)
    session.refresh(hunt)

    session.add(CampaignHunt(campaign_id=campaign.id, hunt_id=hunt.id))
    session.add(Run(hunt_id=hunt.id, outcome=RunOutcome.no_findings, notes="Oldest", ran_at=_days_ago(10)))
    session.add(Run(hunt_id=hunt.id, outcome=RunOutcome.findings, notes="Newest", ran_at=_days_ago(1)))
    session.add(Run(hunt_id=hunt.id, outcome=RunOutcome.inconclusive, notes="Middle", ran_at=_days_ago(5)))
    session.commit()
    session.refresh(campaign)

    activity = build_campaign_activity(campaign)

    assert [entry.run.notes for entry in activity] == ["Newest", "Middle", "Oldest"]


def test_activity_excludes_unlinked_hunt_runs(session: Session) -> None:
    campaign = Campaign(title="Campaign", objective="Objective")
    linked_hunt = Hunt(title="Linked", hypothesis="H", status=HuntStatus.active)
    unlinked_hunt = Hunt(title="Unlinked", hypothesis="H", status=HuntStatus.active)
    session.add_all([campaign, linked_hunt, unlinked_hunt])
    session.commit()
    session.refresh(campaign)
    session.refresh(linked_hunt)
    session.refresh(unlinked_hunt)

    session.add(CampaignHunt(campaign_id=campaign.id, hunt_id=linked_hunt.id))
    session.add(Run(hunt_id=linked_hunt.id, outcome=RunOutcome.findings, notes="Belongs here"))
    session.add(Run(hunt_id=unlinked_hunt.id, outcome=RunOutcome.findings, notes="Should not appear"))
    session.commit()
    session.refresh(campaign)

    activity = build_campaign_activity(campaign)

    notes = [entry.run.notes for entry in activity]
    assert "Belongs here" in notes
    assert "Should not appear" not in notes


def test_activity_excludes_runs_from_a_second_unrelated_campaign(session: Session) -> None:
    campaign_a = Campaign(title="Campaign A", objective="Objective A")
    campaign_b = Campaign(title="Campaign B", objective="Objective B")
    hunt_a = Hunt(title="Hunt A", hypothesis="H", status=HuntStatus.active)
    hunt_b = Hunt(title="Hunt B", hypothesis="H", status=HuntStatus.active)
    session.add_all([campaign_a, campaign_b, hunt_a, hunt_b])
    session.commit()
    for obj in (campaign_a, campaign_b, hunt_a, hunt_b):
        session.refresh(obj)

    session.add(CampaignHunt(campaign_id=campaign_a.id, hunt_id=hunt_a.id))
    session.add(CampaignHunt(campaign_id=campaign_b.id, hunt_id=hunt_b.id))
    session.add(Run(hunt_id=hunt_a.id, outcome=RunOutcome.findings, notes="Campaign A activity"))
    session.add(Run(hunt_id=hunt_b.id, outcome=RunOutcome.findings, notes="Campaign B activity"))
    session.commit()
    session.refresh(campaign_a)

    activity = build_campaign_activity(campaign_a)

    notes = [entry.run.notes for entry in activity]
    assert notes == ["Campaign A activity"]


def test_empty_campaign_has_zero_summary_counts_and_no_activity(session: Session) -> None:
    campaign = Campaign(title="Empty", objective="Nothing yet")
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    overview = build_campaign_overview(campaign)
    activity = build_campaign_activity(campaign)

    assert overview.total_linked_hunts == 0
    assert overview.active_hunts == 0
    assert overview.hunts_with_findings == 0
    assert overview.hunt_cards == []
    assert activity == []


def test_overview_counts_active_hunts_and_findings_correctly(session: Session) -> None:
    campaign = Campaign(title="Campaign", objective="Objective")
    active_with_findings = Hunt(title="Active w/ findings", hypothesis="H", status=HuntStatus.active)
    active_without_findings = Hunt(title="Active clean", hypothesis="H", status=HuntStatus.active)
    idea_hunt = Hunt(title="Just an idea", hypothesis="H", status=HuntStatus.idea)
    session.add_all([campaign, active_with_findings, active_without_findings, idea_hunt])
    session.commit()
    for obj in (campaign, active_with_findings, active_without_findings, idea_hunt):
        session.refresh(obj)

    for hunt in (active_with_findings, active_without_findings, idea_hunt):
        session.add(CampaignHunt(campaign_id=campaign.id, hunt_id=hunt.id))
    session.add(Run(hunt_id=active_with_findings.id, outcome=RunOutcome.findings, notes="Hit"))
    session.add(Run(hunt_id=active_without_findings.id, outcome=RunOutcome.no_findings, notes="Clean"))
    session.commit()
    session.refresh(campaign)

    overview = build_campaign_overview(campaign)

    assert overview.total_linked_hunts == 3
    assert overview.active_hunts == 2
    assert overview.hunts_with_findings == 1
