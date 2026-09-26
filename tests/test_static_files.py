import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

APP_DIR = Path(__file__).resolve().parent.parent / "app"


def test_static_files_force_revalidation_so_edits_are_never_stale() -> None:
    client = TestClient(app)

    for path in ("/static/filter.js", "/static/themes/terminal-console.css"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"
        assert "etag" in response.headers


def test_unchanged_static_file_revalidates_with_304() -> None:
    client = TestClient(app)
    etag = client.get("/static/filter.js").headers["etag"]

    response = client.get("/static/filter.js", headers={"If-None-Match": etag})

    assert response.status_code == 304


def test_filter_hides_the_element_that_wraps_each_board_card() -> None:
    filter_js = (APP_DIR / "static" / "filter.js").read_text(encoding="utf-8")
    card_html = (APP_DIR / "templates" / "partials" / "card.html").read_text(encoding="utf-8")
    css = (APP_DIR / "static" / "themes" / "terminal-console.css").read_text(encoding="utf-8")

    wrapper = re.search(r'card\.closest\("\.([\w-]+)"\)', filter_js)
    assert wrapper is not None
    wrapper_class = wrapper.group(1)

    assert card_html.lstrip().startswith(f'<a class="{wrapper_class}"')
    assert f".{wrapper_class}[hidden] {{ display: none; }}" in css
