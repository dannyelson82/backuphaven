"""BackupHaven: simple, plugin-based, automated nightly backups for Unraid apps."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("backuphaven")
except PackageNotFoundError:  # pragma: no cover - only when run from a bare source tree
    __version__ = "0.0.0"
