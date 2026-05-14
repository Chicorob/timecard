from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from ..config import get_settings
from ..db import init_db
from ..kantata_client import KantataAPIError
from .routes import audit as audit_routes
from .routes import auth as auth_routes
from .routes import entries as entries_routes

logger = logging.getLogger(__name__)

PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(PACKAGE_ROOT / "templates"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    await init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    is_https = settings.base_url.startswith("https://")
    app = FastAPI(title="Kantata Time-Card Adjustments", lifespan=lifespan)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        https_only=is_https,
        same_site="lax",
        max_age=60 * 60 * 24 * 7,  # 7 days
    )

    static_dir = PACKAGE_ROOT / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    else:
        logger.warning("Static files directory %s not found; /static will not be served", static_dir)
    app.include_router(auth_routes.router)
    app.include_router(entries_routes.router)
    app.include_router(audit_routes.router)

    @app.get("/", response_class=HTMLResponse)
    async def landing(request: Request):
        if request.session.get("user_id"):
            return RedirectResponse(url="/entries", status_code=303)
        return TEMPLATES.TemplateResponse(request, "landing.html", {})

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.exception_handler(KantataAPIError)
    async def kantata_error_handler(request: Request, exc: KantataAPIError):
        return JSONResponse(
            status_code=exc.status_code or 502,
            content={"error": str(exc), "body": exc.body},
        )

    return app


app = create_app()
