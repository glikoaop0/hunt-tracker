from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models import Hunt, HuntStatus, RetirementReason

PAST = date.today() - timedelta(days=10)


def test_active_hunt_with_past_next_run_is_overdue() -> None:
    hunt = Hunt(title="t", hypothesis="h", status=HuntStatus.active, cadence_days=7, next_run=PAST)
    assert hunt.is_overdue is True


def test_retired_hunt_is_never_overdue_but_keeps_its_schedule() -> None:
    hunt = Hunt(title="t", hypothesis="h", status=HuntStatus.retired, cadence_days=7, next_run=PAST)
    assert hunt.is_overdue is False
    assert hunt.next_run == PAST
    assert hunt.cadence_days == 7


@pytest.fixture()
def client_and_ids() -> Iterator[tuple[TestClient, int]]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def override_get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with Session(engine) as session:
        retired = Hunt(
            title="Retired recurring hunt",
            hypothesis="h",
            status=HuntStatus.retired,
            cadence_days=7,
            next_run=PAST,
            retirement_reason=RetirementReason.converted_to_detection,
            retired_at=datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
        )
        session.add(retired)
        session.commit()
        session.refresh(retired)
        retired_id = retired.id

    yield TestClient(app), retired_id

    app.dependency_overrides.clear()


def test_board_card_for_retired_hunt_shows_retired_date_not_next_run(
    client_and_ids: tuple[TestClient, int],
) -> None:
    client, _ = client_and_ids
    html = " ".join(client.get("/").text.split())

    assert "Retired on 2026-09-20" in html
    assert "Converted to detection" in html
    assert f"Next run {PAST}" not in html
    assert 'data-overdue="true"' not in html
    assert '<span class="summary-value">0</span> <span class="summary-label">Overdue</span>' in html


def test_detail_for_retired_hunt_says_not_scheduled(client_and_ids: tuple[TestClient, int]) -> None:
    client, retired_id = client_and_ids
    html = client.get(f"/hunts/{retired_id}").text

    assert "Not scheduled &mdash; retired" in html
    assert "badge-overdue" not in html
