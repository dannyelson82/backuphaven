"""Plugin manifests, ``plugin.yaml`` (ARCHITECTURE.md §6.3, ADR-0003). Plugin API v1.

A manifest is data: it is validated here and only ever run by BackupHaven's own
built-in actions. Every path pattern is relative to the app's data folder and may
not leave it (SECURITY.md "Plugins").
"""

import re
from typing import Annotated, Literal

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

PLUGIN_API = 1
MAX_MANIFEST_BYTES = 64 * 1024
MAX_PATTERNS = 200

_ID = r"^[a-z0-9][a-z0-9-]{0,63}$"
_VERSION = r"^\d{1,4}\.\d{1,4}\.\d{1,4}$"
_CONTAINER_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_FORBIDDEN_IN_PATTERN = re.compile(r"\\|\x00|^/|(^|/)\.\.(/|$)")


def _check_pattern(value: str) -> str:
    """A glob relative to the app's folder: no absolute paths, no ``..``, no backslashes.
    ``Backups/`` means the folder and everything in it (``Backups/**``)."""
    value = value.strip()
    if not value or len(value) > 512:
        raise ValueError("pattern must be 1 to 512 characters")
    if _FORBIDDEN_IN_PATTERN.search(value):
        raise ValueError(f"pattern {value!r} must stay inside the app's folder")
    if value.endswith("/"):
        value = value.rstrip("/") + "/**"
    return value


Pattern = Annotated[str, AfterValidator(_check_pattern)]


def _check_container_path(value: str) -> str:
    """An absolute path inside the app's container made of plain names (no ``.``/``..``)."""
    parts = value.split("/")
    if len(value) > 256 or parts[0] != "" or len(parts) < 2:
        raise ValueError("must be an absolute path such as /config")
    if value != "/" and not all(
        _CONTAINER_NAME.fullmatch(p) and p not in (".", "..") for p in parts[1:]
    ):
        raise ValueError(f"{value!r} must be a plain path such as /config")
    return value


ContainerPath = Annotated[str, AfterValidator(_check_container_path)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FileRules(Strict):
    include: list[Pattern] = Field(default_factory=list, max_length=MAX_PATTERNS)
    exclude: list[Pattern] = Field(default_factory=list, max_length=MAX_PATTERNS)


class Recognise(Strict):
    # Image name patterns without registry tag, e.g. "*/sonarr" or "sonarr".
    images: list[Annotated[str, StringConstraints(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=50
    )


class Backup(FileRules):
    mode: Literal["snapshot", "stop"] = "snapshot"
    # The app's data folder, as the app's container sees it. "*" = every data folder
    # (the generic plugin).
    folder: Literal["*"] | ContainerPath | list[ContainerPath] = "/config"
    # Container paths mapped to a *whole share* that holds the app's own data (e.g.
    # PlanHaven's attachments at /data). Whole shares are otherwise never backed up.
    share_folders: list[ContainerPath] = Field(default_factory=list, max_length=10)
    # SQLite databases to snapshot and check (globs). "auto": find them by their header.
    databases: list[Pattern] | Literal["auto"] = Field(default_factory=list)
    # Files that must exist, or the app's backup fails.
    expect: list[Pattern] = Field(default_factory=list, max_length=MAX_PATTERNS)


class Setting(Strict):
    id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,40}$")]
    label: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    help: Annotated[str, StringConstraints(max_length=300)] = ""
    type: Literal["boolean"] = "boolean"
    default: bool = False
    when_true: FileRules = Field(default_factory=FileRules)


class Restore(Strict):
    stop_first: bool = True


class Manifest(Strict):
    id: Annotated[str, StringConstraints(pattern=_ID)]
    name: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    version: Annotated[str, StringConstraints(pattern=_VERSION)]
    kind: Literal["app"]  # destination and alert plugins come later (0.6)
    plugin_api: Literal[1]
    author: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    description: Annotated[str, StringConstraints(max_length=500)] = ""
    recognise: Recognise = Field(default_factory=Recognise)
    backup: Backup = Field(default_factory=Backup)
    settings: list[Setting] = Field(default_factory=list, max_length=20)
    restore: Restore = Field(default_factory=Restore)

    @model_validator(mode="after")
    def _unique_settings(self) -> "Manifest":
        ids = [s.id for s in self.settings]
        if len(ids) != len(set(ids)):
            raise ValueError("setting ids must be unique")
        return self


class ManifestError(ValueError):
    """A plugin.yaml that isn't a valid plugin API v1 manifest."""


def parse_manifest(text: str) -> Manifest:
    """Validate a ``plugin.yaml``. YAML is read with ``safe_load`` (no tags, no code)."""
    if len(text.encode()) > MAX_MANIFEST_BYTES:
        raise ManifestError("plugin.yaml is too large")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"not valid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise ManifestError("plugin.yaml must be a mapping")
    try:
        return Manifest.model_validate(data)
    except ValidationError as exc:
        raise ManifestError(str(exc)) from None
