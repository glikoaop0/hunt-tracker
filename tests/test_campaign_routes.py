from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models import Campaign, CampaignHunt, Hunt, HuntStatus, Run, RunOutcome


@pytest.fixture()
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    with Session(engine) as session:
        kerberos_hunt = Hunt(
            title="Kerberoasting attempts",
            hypothesis="Service accounts targeted for offline cracking",
            status=HuntStatus.active,
            priority=1,
        )
        lateral_hunt = Hunt(
            title="Lateral movement via WMI",
            hypothesis="WMI used to execute commands on remote hosts",
            status=HuntStatus.idea,
            priority=3,
        )
        unrelated_hunt = Hunt(
            title="Unrelated phishing hunt",
            hypothesis="Nothing to do with AD",
            status=HuntStatus.active,
            priority=2,
        )
        session.add_all([kerberos_hunt, lateral_hunt, unrelated_hunt])
        session.commit()
        for hunt in (kerberos_hunt, lateral_hunt, unrelated_hunt):
            session.refresh(hunt)

        campaign = Campaign(title="AD Compromise Investigation", objective="Determine whether AD was compromised.")
        session.add(campaign)
        session.commit()
        session.refresh(campaign)

        session.add(CampaignHunt(campaign_id=campaign.id, hunt_id=kerberos_hunt.id))
        session.add(
            Run(hunt_id=kerberos_hunt.id, outcome=RunOutcome.findings, notes="Confirmed kerberoasting on svc-sql.")
        )
        session.add(Run(hunt_id=unrelated_hunt.id, outcome=RunOutcome.findings, notes="Unrelated finding."))
        session.commit()

    yield TestClient(app, follow_redirects=False)

    app.dependency_overrides.clear()


def test_campaigns_list_shows_title_status_group_and_counts(client: TestClient) -> None:
    response = client.get("/campaigns")
    assert response.status_code == 200
    html = " ".join(response.text.split())
    assert "AD Compromise Investigation" in html
    assert 'campaign-group-title">Planned' in html
    assert "<dt>Hunts</dt><dd>1</dd>" in html
    assert "<dt>Runs</dt><dd>1</dd>" in html
    assert "1 active" in html


def test_campaigns_list_groups_by_status_in_fixed_order(client: TestClient) -> None:
    for title, status in (("Closed one", "closed"), ("Active one", "active")):
        location = client.post("/campaigns", data={"title": title, "objective": "x"}).headers["location"]
        client.post(f"{location}/status", data={"status": status})

    html = client.get("/campaigns").text

    active = html.index('campaign-group-title">Active')
    planned = html.index('campaign-group-title">Planned')
    closed = html.index('campaign-group-title">Closed')
    assert active < planned < closed


def test_campaigns_list_row_without_hunts_says_so(client: TestClient) -> None:
    client.post("/campaigns", data={"title": "Empty campaign", "objective": "Nothing linked."})

    assert "No hunts linked yet" in client.get("/campaigns").text


def test_new_campaign_form_shows_how_campaigns_work_guide(client: TestClient) -> None:
    response = client.get("/campaigns/new")
    assert response.status_code == 200
    assert "How campaigns work" in response.text
    assert "Link hunts." in response.text


def test_campaigns_list_empty_state() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def override_get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        empty_client = TestClient(app)
        response = empty_client.get("/campaigns")
        assert response.status_code == 200
        assert "No campaigns yet" in response.text
    finally:
        app.dependency_overrides.clear()


def test_new_campaign_form_renders(client: TestClient) -> None:
    response = client.get("/campaigns/new")
    assert response.status_code == 200
    assert "New campaign" in response.text


def test_create_campaign_requires_title_and_preserves_objective(client: TestClient) -> None:
    response = client.post("/campaigns", data={"title": "", "objective": "Keep me"})
    assert response.status_code == 422
    assert "Title is required" in response.text
    assert "Keep me" in response.text


def test_create_campaign_redirects_to_detail(client: TestClient) -> None:
    response = client.post(
        "/campaigns", data={"title": "New Investigation", "objective": "Find the thing."}
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/campaigns/")


def test_campaign_detail_overview_shows_summary_counts(client: TestClient) -> None:
    response = client.get("/campaigns/1")
    assert response.status_code == 200
    assert "Linked hunts" in response.text
    assert "Kerberoasting attempts" in response.text
    # one linked hunt, currently active, with one Findings run
    assert ">1<" in response.text.replace("\n", "").replace(" ", "")


def test_campaign_detail_activity_tab_shows_linked_hunt_runs_only(client: TestClient) -> None:
    response = client.get("/campaigns/1?tab=activity")
    assert response.status_code == 200
    assert "Confirmed kerberoasting on svc-sql." in response.text
    assert "Unrelated finding." not in response.text


def test_campaign_detail_unknown_id_404s(client: TestClient) -> None:
    response = client.get("/campaigns/999")
    assert response.status_code == 404


def test_hunt_picker_excludes_already_linked_hunts(client: TestClient) -> None:
    response = client.get("/campaigns/1/hunt-picker", params={"q": "a"})
    assert response.status_code == 200
    assert "Kerberoasting attempts" not in response.text
    assert "Lateral movement via WMI" in response.text


def test_link_hunt_route_adds_membership_and_prevents_duplicate(client: TestClient) -> None:
    response = client.post("/campaigns/1/hunts", data={"hunt_id": 2})
    assert response.status_code == 200
    assert "Lateral movement via WMI" in response.text

    # duplicate link should not error and should not create a second row
    response = client.post("/campaigns/1/hunts", data={"hunt_id": 2})
    assert response.status_code == 200
    assert response.text.count("Lateral movement via WMI") == 1


def test_unlink_hunt_route_removes_membership_without_touching_hunt(client: TestClient) -> None:
    response = client.post("/campaigns/1/hunts/1/unlink")
    assert response.status_code == 200
    assert "Kerberoasting attempts" not in response.text

    hunt_response = client.get("/hunts/1")
    assert hunt_response.status_code == 200
    assert hunt_response.text.count('badge-status-active">Active') >= 1  # hunt status untouched


def test_hunt_detail_shows_campaign_backlink(client: TestClient) -> None:
    response = client.get("/hunts/1")
    assert response.status_code == 200
    assert "AD Compromise Investigation" in response.text


def test_hunt_detail_no_backlink_after_unlink(client: TestClient) -> None:
    client.post("/campaigns/1/hunts/1/unlink")
    response = client.get("/hunts/1")
    assert response.status_code == 200
    assert "Not linked to any campaign yet" in response.text


def test_close_campaign_does_not_retire_linked_hunt(client: TestClient) -> None:
    response = client.post("/campaigns/1/status", data={"status": "closed"})
    assert response.status_code == 200
    assert "Closed" in response.text

    hunt_response = client.get("/hunts/1")
    assert 'aria-current="step"><span class="lifecycle-step-label">Active</span>' in hunt_response.text
    assert 'class="retired-banner"' not in hunt_response.text


def test_reopen_closed_campaign(client: TestClient) -> None:
    client.post("/campaigns/1/status", data={"status": "closed"})
    response = client.post("/campaigns/1/status", data={"status": "active"})
    assert response.status_code == 200
    assert "Reopen campaign" not in response.text


def test_edit_campaign_form_prefills_values(client: TestClient) -> None:
    response = client.get("/campaigns/1/edit")
    assert response.status_code == 200
    assert "AD Compromise Investigation" in response.text


def test_edit_campaign_form_shows_guide(client: TestClient) -> None:
    response = client.get("/campaigns/1/edit")
    assert response.status_code == 200
    assert "Good to know" in response.text
    assert "the hunt and its runs are kept" in response.text


def test_edit_campaign_validation_error_preserves_values(client: TestClient) -> None:
    response = client.post(
        "/campaigns/1/edit",
        data={"title": "", "objective": "Kept objective", "status": "active"},
    )
    assert response.status_code == 422
    assert "Kept objective" in response.text


def test_edit_campaign_updates_fields(client: TestClient) -> None:
    response = client.post(
        "/campaigns/1/edit",
        data={
            "title": "Renamed Investigation",
            "objective": "Updated objective",
            "scope": "DCs only",
            "notes": "",
            "conclusion": "",
            "status": "active",
        },
    )
    assert response.status_code == 303

    detail = client.get("/campaigns/1")
    assert "Renamed Investigation" in detail.text
    assert "DCs only" in detail.text


def test_conclusion_inline_save(client: TestClient) -> None:
    response = client.post("/campaigns/1/conclusion", data={"conclusion": "Wrapping up next sprint."})
    assert response.status_code == 200
    assert "Wrapping up next sprint." in response.text


def test_empty_campaign_summary_counts_are_zero(client: TestClient) -> None:
    response = client.post("/campaigns", data={"title": "Empty Campaign", "objective": "Nothing linked yet."})
    detail_url = response.headers["location"]
    detail = client.get(detail_url)
    assert detail.status_code == 200
    assert "No hunts linked yet" in detail.text


def test_board_and_other_htmx_contracts_still_work(client: TestClient) -> None:
    # A light regression check that adding campaigns didn't disturb the board.
    response = client.get("/")
    assert response.status_code == 200
    assert "Kerberoasting attempts" in response.text
