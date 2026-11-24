# syntax=docker/dockerfile:1
# BackupHaven image (ARCHITECTURE.md §12, ADR-0002).
# Built only by GitHub Actions; there is no Docker in the dev environment.

ARG DEBIAN_IMAGE=debian:trixie-slim@sha256:8aa5a7bf5a1955bddbe7d74fcdd0b91ab30e74362a8b73af672b0481b35b44a0
ARG NODE_IMAGE=node:24-trixie-slim@sha256:fe9b16c1666f3b5c89aa27efb5a1233e29767d135bbd3c7afb167b58387c9e7f
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.9

# --- 1. Frontend: static files ------------------------------------------------
FROM ${NODE_IMAGE} AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
COPY docs/guide /src/docs/guide
RUN npm run build

# --- 2. Python: virtual environment against Debian's own Python 3.13 ----------
FROM ${UV_IMAGE} AS uv
FROM ${DEBIAN_IMAGE} AS backend
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv \
 && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON=/usr/bin/python3 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /src/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --locked --no-dev --no-editable

# --- 3. Runtime ---------------------------------------------------------------
# Named so CI can rebuild it without the layer cache (image.yml, no-cache-filters):
# otherwise a cached layer keeps old packages and "apt-get upgrade" never runs.
FROM ${DEBIAN_IMAGE} AS runtime
# Database clients for apps whose database lives in another container (ADR-0014):
# MariaDB's from Debian; PostgreSQL's newest from the PostgreSQL project, since
# pg_dump must be at least as new as the server it dumps.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && install -d -m 0755 /usr/share/postgresql-common/pgdg \
 && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
      https://www.postgresql.org/media/keys/ACCC4CF8.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends python3 tini mariadb-client postgresql-client-18 \
 && apt-get purge -y --auto-remove curl \
 && rm -rf /var/lib/apt/lists/* \
 && install -d -m 0755 /config /backups

COPY --from=backend /app/venv /app/venv
COPY --from=frontend /src/frontend/dist /app/web
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

ENV PATH=/app/venv/bin:${PATH} \
    BACKUPHAVEN_CONFIG_DIR=/config \
    BACKUPHAVEN_BACKUPS_DIR=/backups \
    BACKUPHAVEN_WEB_DIR=/app/web \
    BACKUPHAVEN_PORT=7272 \
    PUID=99 PGID=100 UMASK=022 TZ=Etc/UTC \
    PYTHONUNBUFFERED=1

EXPOSE 7272
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python3", "-c", "import os,urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('BACKUPHAVEN_PORT','7272')}/healthz\", timeout=4)"]

LABEL org.opencontainers.image.title="BackupHaven" \
      org.opencontainers.image.description="Simple, plugin-based, automated nightly backups for the apps on your Unraid server" \
      org.opencontainers.image.source="https://github.com/dannyelson82/backuphaven" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["backuphaven"]
