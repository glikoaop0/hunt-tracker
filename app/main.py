from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from app.csrf import CSRFMiddleware
from app.db import create_db_and_tables
from app.routes import router


class RevalidatingStaticFiles(StaticFiles):
    # Without this, browsers reuse stale JS/CSS after an edit and pair it with new
    # HTML (e.g. an old filter.js looking for markup that no longer exists).
    # no-cache still allows caching; it just forces an ETag check, answered with a cheap 304.
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    create_db_and_tables()
    yield


app = FastAPI(title="Hunt Tracker", lifespan=lifespan)
app.mount(
    "/static",
    RevalidatingStaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
    name="static",
)
app.add_middleware(CSRFMiddleware)
app.include_router(router)
