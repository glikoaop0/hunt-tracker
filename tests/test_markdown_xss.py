import re
from collections.abc import Iterator
from datetime import UTC, datetime
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from markupsafe import Markup
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.main import app
from app.markdown_render import ALLOWED_TAGS, ALLOWED_URL_SCHEMES, render_markdown
from app.models import Campaign, Hunt, HuntStatus, RetirementReason, Run, RunOutcome

PAYLOADS: dict[str, str] = {
    "script tag": "<script>alert('xss')</script>",
    "img onerror": '<img src=x onerror="alert(1)">',
    "markdown javascript link": "[click me](javascript:alert(1))",
    "mixed-case javascript link": "[click me](JaVaScRiPt:alert(1))",
    "entity-encoded javascript link": "[click me](javascript&#58;alert(1))",
    "raw anchor javascript": '<a href="javascript:alert(1)">x</a>',
    "data url link": "[x](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
    "iframe": '<iframe src="https://evil.example"></iframe>',
    "svg onload": "<svg onload=alert(1)><circle r=1></svg>",
    "div event handler": '<div onmouseover="alert(1)">hover</div>',
    "style tag": "<style>body{display:none}</style>",
    "object embed": '<object data="https://evil.example/x.swf"></object><embed src="x">',
    "form": '<form action="https://evil.example"><input name=a></form>',
    "attribute breakout": '<a href="https://ok.example" " onclick="alert(1)">x</a>',
}
ALL_PAYLOADS = "\n\n".join(PAYLOADS.values())


class _TagCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, list[tuple[str, str | None]]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, attrs))


def assert_inert(html: str) -> None:
    collector = _TagCollector()
    collector.feed(html)
    for tag, attrs in collector.tags:
        assert tag in ALLOWED_TAGS, f"disallowed tag <{tag}> in {html!r}"
        for name, value in attrs:
            assert not name.startswith("on"), f"event handler {name} in {html!r}"
            if name in {"href", "src"} and value and ":" in value.split("/", 1)[0]:
                scheme = value.split(":", 1)[0].strip().lower()
                assert scheme in ALLOWED_URL_SCHEMES, f"scheme {scheme!r} in {html!r}"
    lowered = html.lower()
    assert "<script" not in lowered
    assert "javascript:" not in lowered


# --- Renderer ---


@pytest.mark.parametrize("payload", PAYLOADS.values(), ids=PAYLOADS.keys())
def test_render_markdown_neutralizes_payload(payload: str) -> None:
    assert_inert(render_markdown(payload))


def test_render_markdown_drops_script_and_style_content_entirely() -> None:
    html = render_markdown("before <script>alert('xss')</script> <style>p{}</style> after")

    assert "alert" not in html
    assert "p{}" not in html
    assert "before" in html and "after" in html


def test_render_markdown_keeps_text_of_stripped_harmless_tags() -> None:
    assert "hover" in render_markdown('<div onmouseover="alert(1)">hover</div>')


def test_render_markdown_returns_markup_so_templates_need_no_safe_filter() -> None:
    assert isinstance(render_markdown("**hi**"), Markup)
    assert render_markdown(None) == Markup("")


def test_render_markdown_preserves_normal_markdown() -> None:
    html = render_markdown(
        "# Title\n\n"
        "Some **bold**, *italic* and `inline code`.\n\n"
        "- one\n- two\n\n"
        "Steps:\n\n"
        "1. first\n2. second\n\n"
        "> quoted\n\n"
        "---\n\n"
        "[docs](https://example.com/a?b=1&c=2 \"Docs\") and [mail](mailto:soc@example.com) "
        "and ![shot](https://example.com/s.png)\n"
    )

    assert "<h1>Title</h1>" in html
    assert "<strong>bold</strong>" in html and "<em>italic</em>" in html
    assert "<code>inline code</code>" in html
    assert "<ul>" in html and "<ol>" in html and html.count("<li>") == 4
    assert "<blockquote>" in html and "<hr" in html
    assert 'href="https://example.com/a?b=1&amp;c=2"' in html and 'title="Docs"' in html
    assert 'href="mailto:soc@example.com"' in html
    assert 'src="https://example.com/s.png"' in html and 'alt="shot"' in html
    assert_inert(html)


def test_render_markdown_shows_html_inside_code_as_visible_text() -> None:
    html = render_markdown(
        "Inline `<script>alert(1)</script>` and a block:\n\n"
        "    index=edr | where cmd=\"<img src=x onerror=alert(1)>\"\n"
    )

    assert "<pre><code>" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert_inert(html)


def test_render_markdown_caches_repeat_renders() -> None:
    render_markdown.cache_clear()
    render_markdown("cached note")
    render_markdown("cached note")

    info = render_markdown.cache_info()
    assert (info.hits, info.misses) == (1, 1)


# --- Stored XSS through every field the app renders as Markdown ---


@pytest.fixture()
def seeded() -> Iterator[tuple[TestClient, dict[str, int]]]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def override_get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    with Session(engine) as session:
        hunt = Hunt(title="Notes hunt", hypothesis="h", status=HuntStatus.active, notes=ALL_PAYLOADS)
        retired = Hunt(
            title="Retired hunt",
            hypothesis="h",
            status=HuntStatus.retired,
            retirement_reason=RetirementReason.superseded,
            retirement_note=ALL_PAYLOADS,
            retired_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        campaign = Campaign(title="Campaign", objective="o", scope=ALL_PAYLOADS, notes=ALL_PAYLOADS)
        session.add_all([hunt, retired, campaign])
        session.commit()
        run = Run(hunt_id=hunt.id, outcome=RunOutcome.findings, notes=ALL_PAYLOADS)
        session.add(run)
        session.commit()
        ids = {"hunt": hunt.id, "retired": retired.id, "campaign": campaign.id, "run": run.id}

    yield TestClient(app), ids

    app.dependency_overrides.clear()


def rendered_blocks(page: str, container_class: str) -> list[str]:
    return re.findall(rf'<div class="{re.escape(container_class)}">(.*?)</div>', page, flags=re.S)


def assert_page_free_of_payloads(page: str) -> None:
    assert "<script>alert" not in page
    assert "onerror=" not in page
    assert "javascript:" not in page.lower()
    assert "<iframe" not in page
    assert "<svg onload" not in page


def test_hunt_notes_are_sanitized(seeded: tuple[TestClient, dict[str, int]]) -> None:
    client, ids = seeded
    page = client.get(f"/hunts/{ids['hunt']}").text

    blocks = rendered_blocks(page, "prose markdown-body")
    assert len(blocks) == 1
    assert_inert(blocks[0])
    assert_page_free_of_payloads(page)


def test_retirement_note_is_sanitized(seeded: tuple[TestClient, dict[str, int]]) -> None:
    client, ids = seeded
    page = client.get(f"/hunts/{ids['retired']}").text

    blocks = rendered_blocks(page, "retirement-note prose")
    assert len(blocks) == 1
    assert_inert(blocks[0])
    assert_page_free_of_payloads(page)


def test_run_notes_are_sanitized_when_row_expands(seeded: tuple[TestClient, dict[str, int]]) -> None:
    client, ids = seeded
    fragment = client.get(f"/hunts/{ids['hunt']}/runs/{ids['run']}/row?expanded=true").text

    blocks = rendered_blocks(fragment, "run-notes prose")
    assert len(blocks) == 1
    assert_inert(blocks[0])
    assert_page_free_of_payloads(fragment)


def test_campaign_scope_and_notes_are_sanitized(seeded: tuple[TestClient, dict[str, int]]) -> None:
    client, ids = seeded
    page = client.get(f"/campaigns/{ids['campaign']}").text

    blocks = rendered_blocks(page, "prose markdown-body")
    assert len(blocks) == 2
    for block in blocks:
        assert_inert(block)
    assert_page_free_of_payloads(page)


# --- HTMX hardening ---


@pytest.mark.parametrize("path_key", ["/", "hunt", "campaign", "/insights"])
def test_htmx_pages_disable_script_tags_and_eval(
    seeded: tuple[TestClient, dict[str, int]], path_key: str
) -> None:
    client, ids = seeded
    path = {"hunt": f"/hunts/{ids['hunt']}", "campaign": f"/campaigns/{ids['campaign']}"}.get(path_key, path_key)
    page = client.get(path).text

    config = """<meta name="htmx-config" content='{"allowScriptTags": false, "allowEval": false}'>"""
    assert config in page
    assert page.index(config) < page.index('src="/static/htmx.min.js"')


def test_run_row_trigger_needs_no_eval(seeded: tuple[TestClient, dict[str, int]]) -> None:
    client, ids = seeded
    page = client.get(f"/hunts/{ids['hunt']}").text

    assert 'hx-trigger="click"' in page
    assert "keyup[" not in page
    assert 'src="/static/role-button.js"' in page
