"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select

from backuphaven import __version__, settings_store
from backuphaven.api import (
    app_routes,
    auth_routes,
    backup_routes,
    developer_routes,
    keeping_routes,
    run_routes,
    schedule_routes,
    security_routes,
)
from backuphaven.auth.network import IPAddress, read_default_gateways
from backuphaven.auth.throttle import LoginThrottle
from backuphaven.config import Settings, get_settings
from backuphaven.db import Database, Run
from backuphaven.db.types import utcnow
from backuphaven.diagnostics import SETTING_KEY, Diagnostics
from backuphaven.headers import SecurityHeadersMiddleware
from backuphaven.middleware import RequestLogMiddleware
from backuphaven.plugins.registry import bundled
from backuphaven.runner import Runner, remove_leftovers
from backuphaven.scheduler import Scheduler
from backuphaven.secretbox import SecretBox

logger = logging.getLogger(__name__)


def mark_interrupted_runs(db: Database) -> None:
    """A run still "running" at start-up was cut off by a restart."""
    with db.write() as session:
        for run in session.scalars(select(Run).where(Run.status == "running")):
            run.status, run.error, run.finished_at = "failed", "interrupted by a restart", utcnow()


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
    diagnostics = Diagnostics(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db.migrate()
        mark_interrupted_runs(db)
        with db.read() as session:
            developer = settings_store.get(session, SETTING_KEY) or {}
        if developer.get("diagnostics"):
            try:
                diagnostics.enable()
            except OSError:
                logger.warning("diagnostics files couldn't be switched on", exc_info=True)
        removed = remove_leftovers(settings.backups_dir)
        if removed:
            logger.info("removed what an interrupted run left behind", extra={"files": removed})
        app.state.scheduler.start()
        yield
        app.state.scheduler.stop()
        diagnostics.disable()
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
    app.state.diagnostics = diagnostics
    app.state.runner = Runner(db, settings, bundled(), diagnostics)
    app.state.scheduler = Scheduler(db, app.state.runner, settings)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestLogMiddleware)

    @app.get("/healthz")
    def healthz() -> Health:
        return Health(status="ok")

    @app.get("/api/v1/version")
    def version() -> Version:
        return Version(name="BackupHaven", version=__version__)

    for module in (
        auth_routes,
        security_routes,
        app_routes,
        run_routes,
        backup_routes,
        schedule_routes,
        keeping_routes,
        developer_routes,
    ):
        app.include_router(module.router, prefix="/api/v1")

    web_dir = settings.web_dir
    if web_dir is not None and web_dir.is_dir():
        # Mounted last so API routes take precedence over static files.
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app
