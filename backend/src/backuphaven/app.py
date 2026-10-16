"""FastAPI application factory."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backuphaven import __version__
from backuphaven.config import Settings, get_settings
from backuphaven.headers import SecurityHeadersMiddleware
from backuphaven.middleware import RequestLogMiddleware


class Health(BaseModel):
    status: str


class Version(BaseModel):
    name: str
    version: str


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    ``settings.web_dir`` is the built frontend (``frontend/dist``). When it is
    missing, only the API is served, which is normal while developing with
    Vite's dev server.
    """
    settings = settings or get_settings()
    app = FastAPI(
        title="BackupHaven",
        version=__version__,
        docs_url=None,  # API docs only for logged-in users, once login exists (SECURITY.md)
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestLogMiddleware)

    @app.get("/healthz")
    def healthz() -> Health:
        return Health(status="ok")

    @app.get("/api/v1/version")
    def version() -> Version:
        return Version(name="BackupHaven", version=__version__)

    web_dir = settings.web_dir
    if web_dir is not None and web_dir.is_dir():
        # Mounted last so API routes take precedence over static files.
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app
