"""Installed plugins and matching apps to them (ARCHITECTURE.md §6.3).

In phase 0.1 only the plugins bundled in the image exist; they are always trusted.
"""

import fnmatch
import logging
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from backuphaven.plugins.manifest import Manifest, ManifestError, parse_manifest

logger = logging.getLogger(__name__)

BUNDLED_DIR = Path(__file__).parent / "bundled"
GENERIC = "generic"


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

    def get(self, plugin_id: str) -> Manifest | None:
        return self.plugins.get(plugin_id)

    def match(self, image: str) -> Manifest:
        """The plugin for a container image; the generic plugin if none recognises it."""
        name = image_name(image)
        for plugin_id in sorted(self.plugins):
            manifest = self.plugins[plugin_id]
            if any(fnmatch.fnmatchcase(name, p.lower()) for p in manifest.recognise.images):
                return manifest
        return self.plugins[GENERIC]


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
    plugins = load_folder(BUNDLED_DIR)
    if GENERIC not in plugins:  # pragma: no cover - a packaging error
        raise RuntimeError("the bundled generic plugin is missing")
    return Registry(plugins)
