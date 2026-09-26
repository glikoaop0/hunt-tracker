import secrets
from collections.abc import Iterator
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool
from starlette.testclient import TestClient

from app.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME

TEST_CSRF_TOKEN = secrets.token_urlsafe(32)


@pytest.fixture(autouse=True)
def clients_carry_csrf_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Behave like a browser that already loaded a page: every TestClient holds a
    valid session token. tests/test_csrf.py strips it to prove the protection."""
    original_init = TestClient.__init__

    def init_with_token(self: TestClient, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.cookies.set(CSRF_COOKIE_NAME, TEST_CSRF_TOKEN)
        self.headers[CSRF_HEADER_NAME] = TEST_CSRF_TOKEN

    monkeypatch.setattr(TestClient, "__init__", init_with_token)


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
