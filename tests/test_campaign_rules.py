import pytest

from app.models import Campaign, CampaignStatus, Hunt, HuntStatus, RetirementReason, Run, RunOutcome
from app.services import (
    create_campaign,
    link_hunt_to_campaign,
    retire_hunt,
    set_campaign_status,
    unlink_hunt_from_campaign,
    update_campaign,
    update_campaign_conclusion,
)


def make_hunt(hunt_id: int, **overrides) -> Hunt:
    defaults = dict(title="Test hunt", hypothesis="Test hypothesis", status=HuntStatus.active)
    defaults.update(overrides)
    hunt = Hunt(**defaults)
    hunt.id = hunt_id
    return hunt


def make_campaign(campaign_id: int, **overrides) -> Campaign:
    defaults = dict(title="Test campaign", objective="Test objective")
    defaults.update(overrides)
    campaign = Campaign(**defaults)
    campaign.id = campaign_id
    return campaign


def test_create_campaign_requires_title() -> None:
    with pytest.raises(ValueError):
        create_campaign("", "Some objective")


def test_create_campaign_requires_objective() -> None:
    with pytest.raises(ValueError):
        create_campaign("A title", "")


def test_create_campaign_strips_whitespace_and_defaults_status_planned() -> None:
    campaign = create_campaign("  Investigate AD compromise  ", "  Determine scope  ")
    assert campaign.title == "Investigate AD compromise"
    assert campaign.objective == "Determine scope"
    assert campaign.status == CampaignStatus.planned


def test_create_campaign_optional_fields_default_empty() -> None:
    campaign = create_campaign("Title", "Objective")
    assert campaign.scope == ""
    assert campaign.notes == ""
    assert campaign.conclusion == ""


def test_update_campaign_requires_title_and_objective() -> None:
    campaign = make_campaign(1)
    with pytest.raises(ValueError):
        update_campaign(campaign, title="", objective="Still here")
    with pytest.raises(ValueError):
        update_campaign(campaign, title="Still here", objective="")


def test_update_campaign_preserves_fields_on_success() -> None:
    campaign = make_campaign(1)
    update_campaign(
        campaign,
        title="New title",
        objective="New objective",
        scope="In scope: DCs",
        notes="See ticket 123",
        conclusion="Nothing found yet",
    )
    assert campaign.title == "New title"
    assert campaign.scope == "In scope: DCs"
    assert campaign.notes == "See ticket 123"
    assert campaign.conclusion == "Nothing found yet"


def test_update_campaign_conclusion_is_independent_of_full_edit() -> None:
    campaign = make_campaign(1, title="Keep me", objective="Keep me too")
    update_campaign_conclusion(campaign, "  Wrap up next week.  ")
    assert campaign.conclusion == "Wrap up next week."
    assert campaign.title == "Keep me"


def test_set_campaign_status_updates_status() -> None:
    campaign = make_campaign(1)
    assert campaign.status == CampaignStatus.planned
    set_campaign_status(campaign, CampaignStatus.active)
    assert campaign.status == CampaignStatus.active


def test_closing_campaign_does_not_touch_hunt_status() -> None:
    campaign = make_campaign(1)
    hunt = make_hunt(1, status=HuntStatus.active)
    link_hunt_to_campaign(campaign, hunt)

    set_campaign_status(campaign, CampaignStatus.closed)

    assert campaign.status == CampaignStatus.closed
    assert hunt.status == HuntStatus.active


def test_reopening_closed_campaign_does_not_touch_hunt_status() -> None:
    campaign = make_campaign(1)
    hunt = make_hunt(1, status=HuntStatus.retired)
    retire_hunt(hunt, RetirementReason.completed_one_off)
    link_hunt_to_campaign(campaign, hunt)
    set_campaign_status(campaign, CampaignStatus.closed)

    set_campaign_status(campaign, CampaignStatus.active)

    assert campaign.status == CampaignStatus.active
    assert hunt.status == HuntStatus.retired


def test_link_hunt_to_campaign_creates_membership() -> None:
    campaign = make_campaign(1)
    hunt = make_hunt(1)

    link = link_hunt_to_campaign(campaign, hunt)

    assert link.campaign_id == 1
    assert link.hunt_id == 1
    assert hunt in campaign.hunts


def test_link_hunt_to_campaign_rejects_duplicate() -> None:
    campaign = make_campaign(1)
    hunt = make_hunt(1)
    link_hunt_to_campaign(campaign, hunt)

    with pytest.raises(ValueError):
        link_hunt_to_campaign(campaign, hunt)


def test_one_hunt_can_belong_to_multiple_campaigns() -> None:
    hunt = make_hunt(1)
    campaign_a = make_campaign(1, title="Campaign A")
    campaign_b = make_campaign(2, title="Campaign B")

    link_hunt_to_campaign(campaign_a, hunt)
    link_hunt_to_campaign(campaign_b, hunt)

    assert hunt in campaign_a.hunts
    assert hunt in campaign_b.hunts
    assert {c.id for c in hunt.campaigns} == {1, 2}


def test_unlink_hunt_from_campaign_rejects_foreign_membership() -> None:
    campaign_a = make_campaign(1)
    campaign_b = make_campaign(2)
    hunt = make_hunt(1)
    link = link_hunt_to_campaign(campaign_a, hunt)

    with pytest.raises(ValueError):
        unlink_hunt_from_campaign(campaign_b, link)


def test_unlink_hunt_from_campaign_never_modifies_the_hunt() -> None:
    campaign = make_campaign(1)
    hunt = make_hunt(1, title="Untouched", hypothesis="Untouched hypothesis", priority=4)
    link = link_hunt_to_campaign(campaign, hunt)

    unlink_hunt_from_campaign(campaign, link)

    assert hunt.title == "Untouched"
    assert hunt.hypothesis == "Untouched hypothesis"
    assert hunt.priority == 4
    assert hunt.status == HuntStatus.active
