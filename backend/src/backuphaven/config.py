"""Process settings, read from environment variables (ARCHITECTURE.md §5.2).

User-editable settings will live in BackupHaven's own database; this module
only covers what must be known before it is opened.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["debug", "info", "warning", "error"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BACKUPHAVEN_", extra="ignore", populate_by_name=True
    )

    # The container sets BACKUPHAVEN_CONFIG_DIR=/config. The development default
    # is deliberately a local folder: in code-server, /config is the home dir.
    config_dir: Path = Path(".dev-config")
    # Where archives are written (the built-in local destination).
    backups_dir: Path = Path(".dev-backups")
    web_dir: Path | None = None
    # Unraid's container templates (read-only /boot mount in the container).
    templates_dir: Path = Path("/boot/config/plugins/dockerMan/templates-user")
    # Data shares are mounted here at their own host paths (ADR-0009).
    data_root: Path = Path("/mnt/user")
    # Address of the optional socket proxy (ADR-0005); None: stop mode unavailable.
    docker_proxy: str | None = None
    # Owner of the files BackupHaven creates (Unraid's PUID/PGID, no prefix).
    puid: int = Field(default=99, ge=0, validation_alias="PUID")
    pgid: int = Field(default=100, ge=0, validation_alias="PGID")
    # A run doesn't start, and stops, when the backups disk has less free space.
    min_free_bytes: int = Field(default=2 << 30, ge=0)
    host: str = "0.0.0.0"  # noqa: S104 - a container must listen on all interfaces
    port: int = Field(default=7272, ge=1, le=65535)
    log_level: LogLevel = "info"
    log_file_max_bytes: int = 10 * 1024 * 1024
    log_file_backups: int = 5
    # Diagnostics files (Settings → Developer); None: /diagnostics if mapped,
    # otherwise <config>/diagnostics.
    diagnostics_dir: Path | None = None
    # Time zone for the nightly schedule (Unraid passes TZ to every container).
    tz: str = Field(default="Etc/UTC", validation_alias="TZ")

    @property
    def log_dir(self) -> Path:
        return self.config_dir / "logs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
