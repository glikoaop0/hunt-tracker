from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.lifecycle import lifecycle_view
from app.main import app
from app.models import Hunt, HuntStatus, RetirementReason


def make_hunt(status: HuntStatus) -> Hunt:
    return Hunt(title="t", hypothesis="h", status=status)


# --- View model ---


def test_stepper_marks_earlier_stages_done_and_later_upcoming() -> None:
    view = lifecycle_view(make_hunt(HuntStatus.scoped))

    assert [(stage.status, stage.state) for stage in view.stages] == [
        (HuntStatus.idea, "done"),
        (HuntStatus.scoped, "current"),
        (HuntStatus.active, "upcoming"),
        (HuntStatus.retired, "upcoming"),
    ]


@pytest.mark.parametrize(
    ("status", "label", "kind", "target"),
    [
        (HuntStatus.idea, "Scope this hunt", "move", HuntStatus.scoped),
        (HuntStatus.scoped, "Start hunt", "move", HuntStatus.active),
        (HuntStatus.active, "Retire…", "retire", None),
        (HuntStatus.retired, "Reactivate", "reactivate", None),
    ],
)
def test_primary_action_is_the_natural_next_step(
    status: HuntStatus, label: str, kind: str, target: HuntStatus | None
) -> None:
    primary = lifecycle_view(make_hunt(status)).primary

    assert (primary.label, primary.kind, primary.status) == (label, kind, target)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (HuntStatus.idea, [("retire", None)]),
        (HuntStatus.scoped, [("move", HuntStatus.idea), ("retire", None)]),
        (HuntStatus.active, [("move", HuntStatus.scoped)]),
        (HuntStatus.retired, []),
    ],
)
def test_menu_offers_move_back_and_retire_early(
    status: HuntStatus, expected: list[tuple[str, HuntStatus | None]]
) -> None:
    others = lifecycle_view(make_hunt(status)).others

    assert [(action.kind, action.status) for action in others] == expected


# --- Routes ---


@pytest.fixture()
def client() -> Iterator[tuple[TestClient, dict[HuntStatus, int]]]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def override_get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    with Session(engine) as session:
        hunts = {
            HuntStatus.idea: Hunt(title="Idea hunt", hypothesis="h", status=HuntStatus.idea),
            HuntStatus.scoped: Hunt(title="Scoped hunt", hypothesis="h", status=HuntStatus.scoped),
            HuntStatus.active: Hunt(title="Active hunt", hypothesis="h", status=HuntStatus.active),
            HuntStatus.retired: Hunt(
                title="Retired hunt",
                hypothesis="h",
                status=HuntStatus.retired,
                retirement_reason=RetirementReason.superseded,
                retired_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        }
        session.add_all(hunts.values())
        session.commit()
        ids = {status: hunt.id for status, hunt in hunts.items()}

    yield TestClient(app), ids

    app.dependency_overrides.clear()


def current_step(html: str) -> str:
    return html.split('aria-current="step"><span class="lifecycle-step-label">', 1)[1].split("<", 1)[0]


def test_detail_page_shows_stepper_primary_action_and_menu(
    client: tuple[TestClient, dict[HuntStatus, int]],
) -> None:
    test_client, ids = client
    html = test_client.get(f"/hunts/{ids[HuntStatus.scoped]}").text

    assert 'aria-label="Lifecycle stage"' in html
    assert current_step(html) == "Scoped"
    assert "Start hunt" in html
    assert '"status": "active"' in html
    assert 'aria-label="More lifecycle actions"' in html
    assert "Move back to Idea" in html
    assert "Retire early…" in html
    assert f"/hunts/{ids[HuntStatus.scoped]}/retire-dialog?context=detail" in html


def test_active_hunt_primary_action_opens_retire_dialog(
    client: tuple[TestClient, dict[HuntStatus, int]],
) -> None:
    test_client, ids = client
    html = test_client.get(f"/hunts/{ids[HuntStatus.active]}").text

    assert "Retire…" in html
    assert 'aria-haspopup="dialog"' in html
    assert "Move back to Scoped" in html


def test_retired_hunt_has_reactivate_and_no_menu(
    client: tuple[TestClient, dict[HuntStatus, int]],
) -> None:
    test_client, ids = client
    html = test_client.get(f"/hunts/{ids[HuntStatus.retired]}").text

    assert current_step(html) == "Retired"
    assert f'hx-post="/hunts/{ids[HuntStatus.retired]}/reactivate"' in html
    assert 'aria-label="More lifecycle actions"' not in html


def test_move_route_advances_stage_and_returns_detail(
    client: tuple[TestClient, dict[HuntStatus, int]],
) -> None:
    test_client, ids = client
    response = test_client.post(f"/hunts/{ids[HuntStatus.idea]}/move", data={"status": "scoped"})

    assert response.status_code == 200
    assert 'id="hunt-main"' in response.text
    assert current_step(response.text) == "Scoped"
    assert current_step(test_client.get(f"/hunts/{ids[HuntStatus.idea]}").text) == "Scoped"


def test_move_route_can_move_back_a_stage(client: tuple[TestClient, dict[HuntStatus, int]]) -> None:
    test_client, ids = client
    response = test_client.post(f"/hunts/{ids[HuntStatus.active]}/move", data={"status": "scoped"})

    assert response.status_code == 200
    assert current_step(response.text) == "Scoped"


def test_move_route_refuses_retirement_without_a_reason(
    client: tuple[TestClient, dict[HuntStatus, int]],
) -> None:
    test_client, ids = client
    response = test_client.post(f"/hunts/{ids[HuntStatus.active]}/move", data={"status": "retired"})

    assert response.status_code == 400
    assert current_step(test_client.get(f"/hunts/{ids[HuntStatus.active]}").text) == "Active"


def test_move_route_rejects_unknown_status_and_unknown_hunt(
    client: tuple[TestClient, dict[HuntStatus, int]],
) -> None:
    test_client, ids = client

    assert test_client.post(f"/hunts/{ids[HuntStatus.idea]}/move", data={"status": "bogus"}).status_code == 422
    assert test_client.post("/hunts/999/move", data={"status": "scoped"}).status_code == 404


def test_retire_dialog_from_detail_targets_hunt_page(
    client: tuple[TestClient, dict[HuntStatus, int]],
) -> None:
    test_client, ids = client
    html = test_client.get(f"/hunts/{ids[HuntStatus.active]}/retire-dialog?context=detail").text

    assert 'hx-target="#hunt-main"' in html
    assert 'name="context" value="detail"' in html


def test_retire_dialog_defaults_to_board_and_rejects_unknown_context(
    client: tuple[TestClient, dict[HuntStatus, int]],
) -> None:
    test_client, ids = client
    html = test_client.get(f"/hunts/{ids[HuntStatus.active]}/retire-dialog").text

    assert 'hx-target="#board"' in html
    assert 'name="context" value="board"' in html
    assert test_client.get(f"/hunts/{ids[HuntStatus.active]}/retire-dialog?context=x").status_code == 422


def test_retire_from_detail_returns_hunt_page_and_closes_dialog(
    client: tuple[TestClient, dict[HuntStatus, int]],
) -> None:
    test_client, ids = client
    response = test_client.post(
        f"/hunts/{ids[HuntStatus.active]}/retire",
        data={"reason": "hypothesis_rejected", "context": "detail"},
    )

    assert response.status_code == 200
    assert 'id="hunt-main"' in response.text
    assert current_step(response.text) == "Retired"
    assert "Retired: <strong>Hypothesis rejected</strong>" in response.text
    assert '<div id="retire-dialog-root" hx-swap-oob="true"></div>' in response.text


def test_retire_from_board_still_returns_board(
    client: tuple[TestClient, dict[HuntStatus, int]],
) -> None:
    test_client, ids = client
    response = test_client.post(f"/hunts/{ids[HuntStatus.active]}/retire", data={"reason": "superseded"})

    assert response.status_code == 200
    assert 'data-status="retired"' in response.text
    assert 'id="hunt-main"' not in response.text


def test_board_cards_are_drag_only_with_a_drag_hint(client: tuple[TestClient, dict[HuntStatus, int]]) -> None:
    test_client, _ = client
    html = test_client.get("/").text

    assert 'draggable="true"' in html
    assert 'class="board-hint"' in html
    assert "Drag cards between columns to change their stage" in html
    assert "Move to" not in html
    assert 'class="menu' not in html


def test_empty_column_invites_dragging(client: tuple[TestClient, dict[HuntStatus, int]]) -> None:
    test_client, ids = client
    test_client.post(f"/hunts/{ids[HuntStatus.idea]}/move", data={"status": "scoped"})

    html = test_client.get("/").text
    idea_column = html.split('data-status="idea"', 1)[1].split('data-status="scoped"', 1)[0]

    assert "Drag a hunt here" in idea_column
