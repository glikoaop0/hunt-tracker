import hmac
import re
import secrets
from urllib.parse import parse_qs, urlsplit

from jinja2 import pass_context
from jinja2.runtime import Context
from markupsafe import Markup
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSRF_COOKIE_NAME = "csrftoken"
CSRF_HEADER_NAME = "x-csrf-token"
CSRF_FORM_FIELD = "csrf_token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _cookie_token(headers: Headers) -> str | None:
    token = cookie_parser(headers.get("cookie", "")).get(CSRF_COOKIE_NAME)
    return token if token and _TOKEN_PATTERN.fullmatch(token) else None


def _origin_of(url: str) -> str | None:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def _cross_origin_reason(scope: Scope, headers: Headers) -> str | None:
    expected = f"{scope['scheme']}://{headers.get('host', '')}".lower()
    origin = headers.get("origin")
    if origin is not None:
        return None if origin.lower() == expected else "Cross-origin request blocked."
    referer = headers.get("referer")
    if referer is not None and _origin_of(referer) != expected:
        return "Cross-origin request blocked."
 
    return None


async def _read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _replaying(body: bytes, receive: Receive) -> Receive:
    sent = False

    async def replay() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


def _tokens_match(submitted: str | None, expected: str) -> bool:
    if not submitted:
        return False
    return hmac.compare_digest(submitted.encode("utf-8"), expected.encode("utf-8"))


class CSRFMiddleware:
    """Double-submit-cookie CSRF protection for every unsafe method.

    A random per-session token lives in an HttpOnly, SameSite=Strict session
    cookie; mutations must echo it in the X-CSRF-Token header (HTMX) or the
    csrf_token form field (plain forms). The check runs before routing, so no
    route can mutate state without it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        existing = _cookie_token(headers)
        token = existing or _new_token()
        scope.setdefault("state", {})["csrf_token"] = token

        if scope["method"] in UNSAFE_METHODS:
            failure = _cross_origin_reason(scope, headers)
            if failure is None:
                submitted = headers.get(CSRF_HEADER_NAME)
                if submitted is None and headers.get("content-type", "").startswith(_FORM_CONTENT_TYPE):
                    body = await _read_body(receive)
                    receive = _replaying(body, receive)
                    values = parse_qs(body.decode("utf-8", errors="replace")).get(CSRF_FORM_FIELD)
                    submitted = values[0] if values else None
                if existing is None or not _tokens_match(submitted, existing):
                    failure = "CSRF check failed. Reload the page and try again."
            if failure is not None:
                await JSONResponse({"detail": failure}, status_code=403)(scope, receive, send)
                return

        # Static files are fetched after the page that set the cookie; skipping
        # them avoids racing a second fresh token over the one in the page.
        if existing is None and not scope["path"].startswith("/static/"):
            send = _setting_cookie(send, token)
        await self.app(scope, receive, send)


def _setting_cookie(send: Send, token: str) -> Send:
    async def send_with_cookie(message: Message) -> None:
        if message["type"] == "http.response.start":
            MutableHeaders(scope=message).append(
                "set-cookie", f"{CSRF_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict"
            )
        await send(message)

    return send_with_cookie


@pass_context
def csrf_token(context: Context) -> str:
    return context["request"].state.csrf_token


@pass_context
def csrf_field(context: Context) -> Markup:
    return Markup('<input type="hidden" name="{}" value="{}">').format(CSRF_FORM_FIELD, csrf_token(context))
