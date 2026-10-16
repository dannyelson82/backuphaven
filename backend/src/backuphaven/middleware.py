"""HTTP middleware."""

import logging
import time
from urllib.parse import parse_qsl, urlencode

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("backuphaven.request")

# Query parameters that carry credentials and must never be logged (ReelHaven ADR-0011).
_SENSITIVE_QUERY = frozenset({"apikey", "api_key", "token"})


def safe_query(raw: bytes) -> str:
    pairs = parse_qsl(raw.decode("latin-1"), keep_blank_values=True)
    cleaned = [(k, "[REDACTED]" if k.lower() in _SENSITIVE_QUERY else v) for k, v in pairs]
    return urlencode(cleaned, safe="[]")


class RequestLogMiddleware:
    """Logs one line per HTTP request: method, path, status, duration.

    Headers are never logged, and credential query parameters are redacted.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope["path"]
            if path != "/healthz":  # health checks every few seconds would drown the log
                client = scope.get("client")
                logger.info(
                    "%s %s %s",
                    scope["method"],
                    path,
                    status,
                    extra={
                        "method": scope["method"],
                        "path": path,
                        "query": safe_query(scope.get("query_string", b"")),
                        "status": status,
                        "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                        "client": client[0] if client else None,
                    },
                )
