from functools import lru_cache

import markdown as markdown_lib
import nh3
from markupsafe import Markup

ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "p", "br", "hr",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "strong", "em", "code", "pre", "blockquote",
        "ul", "ol", "li",
        "a", "img",
    }
)
ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
}
ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto"})

@lru_cache(maxsize=1024)
def render_markdown(text: str | None) -> Markup:
    html = markdown_lib.markdown(text or "")
    cleaned = nh3.clean(
        html,
        tags=set(ALLOWED_TAGS),
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=set(ALLOWED_URL_SCHEMES),
    )
    return Markup(cleaned)
