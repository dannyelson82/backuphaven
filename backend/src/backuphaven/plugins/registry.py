"""Plugins and matching apps to them (ARCHITECTURE.md §6.3, ADR-0013).

Plugins only: an app is backed up by a plugin made for it, or not at all.

- **Bundled** plugins ship in the image and have passed their fixture round trip.
- **Local** plugins are recipe files (YAML, never code) the owner puts in
  ``<config>/plugins/`` to try a plugin before it's in a release, e.g. one an AI
  coder wrote from a plugin development file. They are validated like bundled
  ones; a local plugin with a bundled plugin's id replaces it, and is marked
  local everywhere it's shown.
"""

import fnmatch
import logging
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Protocol

from backuphaven.plugins.manifest import Manifest, ManifestError, parse_manifest

logger = logging.getLogger(__name__)

BUNDLED_DIR = Path(__file__).parent / "bundled"
LOCAL_DIR_NAME = "plugins"


def image_name(image: str) -> str:
    """``lscr.io/linuxserver/sonarr:latest@sha256:...`` -> ``lscr.io/linuxserver/sonarr``."""
    name = image.strip().lower().split("@", 1)[0]
    last = name.rsplit("/", 1)[-1]
    if ":" in last:  # a tag, not a registry port (those come before a "/")
        name = name[: len(name) - len(last)] + last.split(":", 1)[0]
    return name


@dataclass(frozen=True)
class Registry:
    plugins: dict[str, Manifest]
    local: frozenset[str] = frozenset()  # ids loaded from the local plugins folder

    def get(self, plugin_id: str) -> Manifest | None:
        return self.plugins.get(plugin_id)

    def match(self, image: str) -> Manifest | None:
        """The plugin for a container image, or None: the app can't be backed up."""
        name = image_name(image)
        if not name:
            return None
        # Local plugins first, so one being tried wins over a bundled one.
        for plugin_id in sorted(self.plugins, key=lambda i: (i not in self.local, i)):
            manifest = self.plugins[plugin_id]
            if any(fnmatch.fnmatchcase(name, p.lower()) for p in manifest.recognise.images):
                return manifest
        return None


def load_folder(folder: Path) -> dict[str, Manifest]:
    plugins: dict[str, Manifest] = {}
    for path in sorted(folder.glob("*.yaml")):
        try:
            manifest = parse_manifest(path.read_text(encoding="utf-8"))
        except (OSError, ManifestError) as exc:
            logger.error("plugin skipped", extra={"file": path.name, "reason": str(exc)})
            continue
        if manifest.id in plugins:
            logger.error("duplicate plugin id", extra={"file": path.name, "id": manifest.id})
            continue
        plugins[manifest.id] = manifest
    return plugins


@cache
def bundled() -> Registry:
    return Registry(load_folder(BUNDLED_DIR))


class PluginSource(Protocol):
    """A ``Registry`` or a ``PluginStore``."""

    @property
    def plugins(self) -> dict[str, Manifest]: ...
    @property
    def local(self) -> frozenset[str]: ...
    def get(self, plugin_id: str) -> Manifest | None: ...
    def match(self, image: str) -> Manifest | None: ...


class PluginStore:
    """Bundled plugins plus the local plugins folder, reloaded when its files change,
    so a plugin dropped in shows up without a restart. Acts as a ``Registry``."""

    def __init__(self, local_dir: Path | None) -> None:
        self.local_dir = local_dir
        self._signature: tuple[tuple[str, int, int], ...] | None = None
        self._registry = bundled()

    def _current_signature(self) -> tuple[tuple[str, int, int], ...]:
        if self.local_dir is None:
            return ()
        try:
            files = sorted(self.local_dir.glob("*.yaml"))
            return tuple((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in files)
        except OSError:
            return ()

    @property
    def registry(self) -> Registry:
        signature = self._current_signature()
        if signature != self._signature:
            local = load_folder(self.local_dir) if self.local_dir and signature else {}
            if local:
                logger.info("local plugins loaded", extra={"plugins": sorted(local)})
            self._registry = Registry({**bundled().plugins, **local}, frozenset(local))
            self._signature = signature
        return self._registry

    @property
    def plugins(self) -> dict[str, Manifest]:
        return self.registry.plugins

    @property
    def local(self) -> frozenset[str]:
        return self.registry.local

    def get(self, plugin_id: str) -> Manifest | None:
        return self.registry.get(plugin_id)

    def match(self, image: str) -> Manifest | None:
        return self.registry.match(image)
