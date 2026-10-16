"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backuphaven import __version__
from backuphaven.api import auth_routes, security_routes
from backuphaven.auth.network import IPAddress, read_default_gateways
from backuphaven.auth.throttle import LoginThrottle
from backuphaven.config import Settings, get_settings
from backuphaven.db import Database
from backuphaven.headers import SecurityHeadersMiddleware
from backuphaven.middleware import RequestLogMiddleware
from backuphaven.secretbox import SecretBox


class Health(BaseModel):
    status: str


class Version(BaseModel):
    name: str
    version: str


def create_app(
    settings: Settings | None = None, gateways: frozenset[IPAddress] | None = None
) -> FastAPI:
    """Build the application.

    ``settings.web_dir`` is the built frontend (``frontend/dist``). When it is
    missing, only the API is served, which is normal while developing with
    Vite's dev server. ``gateways`` overrides Docker gateway detection (tests).
    """
    settings = settings or get_settings()
    db = Database(settings.config_dir / "backuphaven.db")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db.migrate()
        yield
        db.close()

    app = FastAPI(
        title="BackupHaven",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,  # the OpenAPI schema is only for logged-in users (SECURITY.md)
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.secrets = SecretBox(settings.config_dir)
    app.state.throttle = LoginThrottle()
    app.state.gateways = read_default_gateways() if gateways is None else gateways
    app.state.gateway_seen_at = None
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestLogMiddleware)

    @app.get("/healthz")
    def healthz() -> Health:
        return Health(status="ok")

    @app.get("/api/v1/version")
    def version() -> Version:
        return Version(name="BackupHaven", version=__version__)

    for module in (auth_routes, security_routes):
        app.include_router(module.router, prefix="/api/v1")

    web_dir = settings.web_dir
    if web_dir is not None and web_dir.is_dir():
        # Mounted last so API routes take precedence over static files.
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app
