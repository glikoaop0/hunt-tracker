import re
from collections.abc import Iterator

import pytest
from fastapi.routing import APIRoute
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool
from starlette.testclient import TestClient

from app.csrf import CSRF_COOKIE_NAME, CSRF_FORM_FIELD, CSRF_HEADER_NAME
from app.db import get_session
from app.main import app
from app.models import Campaign, Exclusion, Hunt, HuntStatus, Run, RunOutcome
from app.routes import router

ORIGIN = "http://testserver"


@pytest.fixture()
def db() -> Iterator[tuple[object, dict[str, int]]]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def override_get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with Session(engine) as session:
        hunt = Hunt(title="Existing hunt", hypothesis="h", status=HuntStatus.active)
        campaign = Campaign(title="Existing campaign", objective="o")
        session.add_all([hunt, campaign])
        session.commit()
        run = Run(hunt_id=hunt.id, outcome=RunOutcome.no_findings, notes="n")
        session.add(run)
        session.commit()
        ids = {"hunt": hunt.id, "campaign": campaign.id, "run": run.id}

    yield engine, ids

    app.dependency_overrides.clear()


@pytest.fixture()
def browser(db: tuple[object, dict[str, int]]) -> TestClient:
    """A client with no CSRF token yet, like a browser on its first visit."""
    client = TestClient(app)
    client.cookies.clear()
    del client.headers[CSRF_HEADER_NAME]
    return client


def hunt_count(engine: object) -> int:
    with Session(engine) as session:  # type: ignore[arg-type]
        return len(session.exec(select(Hunt)).all())


def meta_token(html: str) -> str:
    match = re.search(r'<meta name="csrf-token" content="([^"]+)">', html)
    assert match, "page has no csrf-token meta tag"
    return match.group(1)


def form_token(html: str) -> str:
    match = re.search(rf'<input type="hidden" name="{CSRF_FORM_FIELD}" value="([^"]+)">', html)
    assert match, "form has no csrf_token field"
    return match.group(1)


NEW_HUNT = {"title": "Via form", "hypothesis": "Posted with a token", "priority": "3"}


# --- Rejections ---


def _post_routes() -> list[str]:
    paths = []
    for route in router.routes:
        if isinstance(route, APIRoute) and route.methods & {"POST", "PUT", "PATCH", "DELETE"}:
            paths.append(re.sub(r"\{[^}]+\}", "1", route.path))
    return sorted(paths)


def test_app_has_mutation_routes_to_protect() -> None:
    assert len(_post_routes()) >= 15


@pytest.mark.parametrize("path", _post_routes())
def test_every_mutation_route_rejects_a_request_without_token(browser: TestClient, path: str) -> None:
    response = browser.post(path, data={"status": "idea"})

    assert response.status_code == 403
    assert "CSRF check failed" in response.json()["detail"]


def test_rejected_mutation_changes_nothing(browser: TestClient, db: tuple[object, dict[str, int]]) -> None:
    engine, _ = db
    before = hunt_count(engine)

    response = browser.post("/hunts", data=NEW_HUNT, follow_redirects=False)

    assert response.status_code == 403
    assert hunt_count(engine) == before


def test_session_cookie_without_submitted_token_is_rejected(browser: TestClient) -> None:
    browser.get("/")

    assert browser.cookies.get(CSRF_COOKIE_NAME)
    assert browser.post("/hunts", data=NEW_HUNT).status_code == 403


def test_wrong_header_token_is_rejected(browser: TestClient, db: tuple[object, dict[str, int]]) -> None:
    _, ids = db
    browser.get("/")
    response = browser.post(
        f"/hunts/{ids['hunt']}/move",
        data={"status": "idea"},
        headers={CSRF_HEADER_NAME: "A" * 43, "HX-Request": "true"},
    )

    assert response.status_code == 403


def test_wrong_form_token_is_rejected(browser: TestClient, db: tuple[object, dict[str, int]]) -> None:
    engine, _ = db
    browser.get("/hunts/new")
    before = hunt_count(engine)

    response = browser.post("/hunts", data={**NEW_HUNT, CSRF_FORM_FIELD: "B" * 43})

    assert response.status_code == 403
    assert hunt_count(engine) == before


def test_malformed_cookie_is_not_accepted_as_a_token(browser: TestClient) -> None:
    browser.cookies.set(CSRF_COOKIE_NAME, "short")

    response = browser.post("/hunts", data=NEW_HUNT, headers={CSRF_HEADER_NAME: "short"})

    assert response.status_code == 403


def test_token_from_another_session_is_rejected(browser: TestClient, db: tuple[object, dict[str, int]]) -> None:
    other = TestClient(app)
    other.cookies.clear()
    del other.headers[CSRF_HEADER_NAME]
    stolen = meta_token(other.get("/").text)
    browser.get("/")

    response = browser.post("/hunts", data={**NEW_HUNT, CSRF_FORM_FIELD: stolen})

    assert response.status_code == 403


# --- Origin / Referer defense in depth ---


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Origin": "http://testserver.evil.example"},
        {"Referer": "https://evil.example/page"},
    ],
    ids=["foreign origin", "null origin", "lookalike origin", "foreign referer"],
)
def test_cross_origin_mutation_is_rejected_even_with_valid_token(
    browser: TestClient, db: tuple[object, dict[str, int]], headers: dict[str, str]
) -> None:
    engine, _ = db
    token = form_token(browser.get("/hunts/new").text)
    before = hunt_count(engine)

    response = browser.post("/hunts", data={**NEW_HUNT, CSRF_FORM_FIELD: token}, headers=headers)

    assert response.status_code == 403
    assert response.json()["detail"] == "Cross-origin request blocked."
    assert hunt_count(engine) == before


@pytest.mark.parametrize(
    "headers",
    [{"Origin": ORIGIN}, {"Referer": f"{ORIGIN}/hunts/new"}, {}],
    ids=["same origin", "same-origin referer", "no origin headers"],
)
def test_same_origin_mutation_with_valid_token_is_accepted(
    browser: TestClient, db: tuple[object, dict[str, int]], headers: dict[str, str]
) -> None:
    token = form_token(browser.get("/hunts/new").text)

    response = browser.post(
        "/hunts", data={**NEW_HUNT, CSRF_FORM_FIELD: token}, headers=headers, follow_redirects=False
    )

    assert response.status_code == 303


# --- Valid submissions ---


def test_valid_normal_form_submission_works(browser: TestClient, db: tuple[object, dict[str, int]]) -> None:
    engine, _ = db
    page = browser.get("/hunts/new")
    token = form_token(page.text)
    before = hunt_count(engine)

    response = browser.post(
        "/hunts", data={**NEW_HUNT, CSRF_FORM_FIELD: token}, headers={"Origin": ORIGIN}, follow_redirects=False
    )

    assert response.status_code == 303
    assert hunt_count(engine) == before + 1
    created = browser.get(response.headers["location"]).text
    assert "Via form" in created and "Posted with a token" in created


def test_valid_htmx_mutation_works(browser: TestClient, db: tuple[object, dict[str, int]]) -> None:
    engine, ids = db
    token = meta_token(browser.get(f"/hunts/{ids['hunt']}").text)

    response = browser.post(
        f"/hunts/{ids['hunt']}/exclusions",
        data={"value": "sccm-01", "reason": "SCCM"},
        headers={CSRF_HEADER_NAME: token, "HX-Request": "true", "Origin": ORIGIN},
    )

    assert response.status_code == 200
    assert "sccm-01" in response.text
    with Session(engine) as session:  # type: ignore[arg-type]
        assert [e.value for e in session.exec(select(Exclusion)).all()] == ["sccm-01"]


def test_valid_htmx_board_move_works(browser: TestClient, db: tuple[object, dict[str, int]]) -> None:
    _, ids = db
    token = meta_token(browser.get("/").text)

    response = browser.post(
        f"/hunts/{ids['hunt']}/status",
        data={"status": "scoped"},
        headers={CSRF_HEADER_NAME: token, "HX-Request": "true"},
    )

    assert response.status_code == 200
    scoped_column = response.text.split('data-status="scoped"', 1)[1].split('data-status="active"', 1)[0]
    assert f'data-hunt-id="{ids["hunt"]}"' in scoped_column


# --- Token issuing and page wiring ---


def test_token_cookie_is_httponly_strict_session_cookie(browser: TestClient) -> None:
    set_cookie = browser.get("/").headers["set-cookie"]

    assert set_cookie.startswith(f"{CSRF_COOKIE_NAME}=")
    assert "HttpOnly" in set_cookie
    assert "SameSite=Strict" in set_cookie
    assert "Path=/" in set_cookie
    assert "Max-Age" not in set_cookie and "Expires" not in set_cookie


def test_token_is_stable_for_the_session(browser: TestClient) -> None:
    first = meta_token(browser.get("/").text)
    second = browser.get("/campaigns/new")

    assert "set-cookie" not in second.headers
    assert form_token(second.text) == first == browser.cookies.get(CSRF_COOKIE_NAME)


def test_static_files_do_not_issue_tokens(browser: TestClient) -> None:
    assert "set-cookie" not in browser.get("/static/csrf.js").headers


@pytest.mark.parametrize(
    "path_template",
    ["/hunts/new", "/hunts/{hunt}/edit", "/hunts/{hunt}/delete", "/campaigns/new", "/campaigns/{campaign}/edit"],
)
def test_every_standard_form_carries_the_session_token(
    browser: TestClient, db: tuple[object, dict[str, int]], path_template: str
) -> None:
    _, ids = db
    html = browser.get(path_template.format(**ids)).text

    assert form_token(html) == browser.cookies.get(CSRF_COOKIE_NAME)
    assert html.count('method="post"') == html.count(f'name="{CSRF_FORM_FIELD}"')


@pytest.mark.parametrize("path_template", ["/", "/hunts/{hunt}", "/campaigns/{campaign}", "/insights"])
def test_every_htmx_page_exposes_token_and_header_script(
    browser: TestClient, db: tuple[object, dict[str, int]], path_template: str
) -> None:
    _, ids = db
    html = browser.get(path_template.format(**ids)).text

    assert meta_token(html) == browser.cookies.get(CSRF_COOKIE_NAME)
    assert html.index('src="/static/htmx.min.js"') < html.index('src="/static/csrf.js"')


def test_htmx_header_script_is_served() -> None:
    script = TestClient(app).get("/static/csrf.js").text

    assert "htmx:configRequest" in script
    assert '"X-CSRF-Token"' in script


# --- Read-only GET routes are unaffected ---

GET_PATHS = [
    "/",
    "/hunts/new",
    "/hunts/{hunt}",
    "/hunts/{hunt}/edit",
    "/hunts/{hunt}/delete",
    "/hunts/{hunt}/retire-dialog",
    "/hunts/{hunt}/runs/{run}/row",
    "/hunts/{hunt}/export/markdown",
    "/campaigns",
    "/campaigns/new",
    "/campaigns/{campaign}",
    "/campaigns/{campaign}/edit",
    "/campaigns/{campaign}/hunt-picker?q=Existing",
    "/insights",
    "/insights/activity/day?date=2026-01-01",
    "/export",
    "/export/json",
]
# Reads hunts.db directly; covered with a patched path in test_export_backup.py.
GET_PATHS_NOT_EXERCISED_HERE = {"/export/backup"}


def test_get_route_list_covers_every_get_route() -> None:
    app_get_paths = {
        route.path for route in router.routes if isinstance(route, APIRoute) and "GET" in route.methods
    }
    covered = {
        re.sub(r"\{(hunt|campaign|run)\}", r"{\1_id}", path.split("?")[0]) for path in GET_PATHS
    }

    assert app_get_paths == covered | GET_PATHS_NOT_EXERCISED_HERE


@pytest.mark.parametrize("path_template", GET_PATHS)
def test_get_routes_work_without_any_token(
    browser: TestClient, db: tuple[object, dict[str, int]], path_template: str
) -> None:
    _, ids = db
    response = browser.get(path_template.format(**ids), headers={"Origin": "https://evil.example"})

    assert response.status_code == 200
