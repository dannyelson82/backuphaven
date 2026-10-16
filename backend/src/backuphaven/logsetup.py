"""Structured JSON logging with secret redaction (SECURITY.md, ReelHaven ADR-0011)."""

import json
import logging
import logging.handlers
import re
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

REDACTED = "[REDACTED]"

# Patterns that look like credentials regardless of whether we know the value.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)(apikey|api_key|x-api-key|token|password|secret)([\"']?\s*[=:]\s*[\"']?)([^\s\"'&,;]+)"
    ),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]+)"),
)
_MIN_SECRET_LENGTH = 6


class SecretRegistry:
    """Known secret values (API keys, tokens) that must never appear in logs."""

    _lock: ClassVar[threading.Lock] = threading.Lock()
    _values: ClassVar[set[str]] = set()

    @classmethod
    def register(cls, value: str) -> None:
        if len(value) >= _MIN_SECRET_LENGTH:
            with cls._lock:
                cls._values.add(value)

    @classmethod
    def unregister(cls, value: str) -> None:
        with cls._lock:
            cls._values.discard(value)

    @classmethod
    def values(cls) -> tuple[str, ...]:
        with cls._lock:
            # Longest first so a secret containing another is fully replaced.
            return tuple(sorted(cls._values, key=len, reverse=True))


def redact(text: str) -> str:
    for value in SecretRegistry.values():
        text = text.replace(value, REDACTED)
    text = _PATTERNS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    return _PATTERNS[1].sub(lambda m: f"{m.group(1)}{REDACTED}", text)


# Attributes every LogRecord has; anything else was passed via ``extra=``.
_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
    | {"message", "asctime", "color_message"}  # uvicorn's ANSI-coloured duplicate
)


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with secrets redacted from every string."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return redact(json.dumps(entry, default=str, ensure_ascii=False))


def configure_logging(level: str, log_dir: Path | None, max_bytes: int, backups: int) -> None:
    """Send all logs (ours and uvicorn's) as JSON to stdout and a rotating file."""
    formatter = JsonFormatter()
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_dir / "backuphaven.log",
                maxBytes=max_bytes,
                backupCount=backups,
                encoding="utf-8",
            )
        )
    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
        old.close()
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(level.upper())

    # Uvicorn: propagate to our root handlers; its access log is replaced by
    # our request middleware (ReelHaven ADR-0011).
    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True
