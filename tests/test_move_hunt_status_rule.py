from datetime import UTC, datetime

from app.models import Hunt, HuntStatus
from app.services import move_hunt


def make_hunt(**overrides) -> Hunt:
    defaults = dict(title="Test hunt", hypothesis="Test hypothesis", status=HuntStatus.idea)
    defaults.update(overrides)
    return Hunt(**defaults)


def test_move_hunt_changes_status() -> None:
    hunt = make_hunt(status=HuntStatus.idea)

    move_hunt(hunt, HuntStatus.active)

    assert hunt.status == HuntStatus.active


def test_move_hunt_updates_updated_at() -> None:
    hunt = make_hunt()
    stale_updated_at = datetime(2020, 1, 1, tzinfo=UTC)
    hunt.updated_at = stale_updated_at

    move_hunt(hunt, HuntStatus.active)

    assert hunt.updated_at > stale_updated_at
