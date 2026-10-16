"""Discovery (ARCHITECTURE.md §6.1, ADR-0009): which containers exist and where their
data lives, read from Unraid's container templates.

``parse_template``, ``normalise_host_path``, ``share_of`` and ``discover_app`` are
pure; ``discover`` reads the template folder.
"""

import logging
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException

logger = logging.getLogger(__name__)

USER_ROOT = PurePosixPath("/mnt/user")
# Folders directly under /mnt that aren't pools or disks holding shares.
_NOT_POOLS = frozenset({"user", "user0", "disks", "remotes", "addons", "rootshare"})
MAX_TEMPLATE_BYTES = 512 * 1024  # real templates are a few KB


@dataclass(frozen=True)
class Mapping:
    """One path mapping from a template: container path ← host path."""

    target: str  # path inside the app's container, e.g. "/config"
    host: str  # path on the Unraid host, e.g. "/mnt/user/appdata/sonarr"
    read_only: bool


@dataclass(frozen=True)
class Template:
    name: str
    image: str
    mappings: tuple[Mapping, ...]


@dataclass(frozen=True)
class DataFolder:
    """A folder (or file) with the app's data, on a data share BackupHaven can see."""

    target: str
    host: str  # normalised to /mnt/user/<share>/...
    share: str


@dataclass(frozen=True)
class MissingShare:
    """Looks like app data, but on a share BackupHaven hasn't been given."""

    target: str
    host: str
    share: str


@dataclass(frozen=True)
class App:
    name: str
    image: str
    folders: tuple[DataFolder, ...] = ()
    missing: tuple[MissingShare, ...] = ()
    template_file: str | None = None  # where the template was read from


class TemplateError(ValueError):
    """A template couldn't be read as a container template."""


def parse_template(text: str) -> Template:
    """Read an Unraid ``dockerMan`` template (XML). Unknown elements are ignored."""
    if len(text.encode()) > MAX_TEMPLATE_BYTES:
        raise TemplateError("template too large")
    try:
        root = SafeET.fromstring(text)
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise TemplateError(f"not valid XML: {exc}") from None
    if root.tag != "Container":
        raise TemplateError("not a container template")
    name = (root.findtext("Name") or "").strip()
    image = (root.findtext("Repository") or "").strip()
    if not name:
        raise TemplateError("template has no name")
    mappings: list[Mapping] = []
    for config in root.iter("Config"):
        if (config.get("Type") or "").strip() != "Path":
            continue
        target = (config.get("Target") or "").strip()
        host = (config.text or "").strip() or (config.get("Default") or "").strip()
        if not target or not host:
            continue
        mode = (config.get("Mode") or "rw").strip().lower()
        mappings.append(Mapping(target=target, host=host, read_only=mode.startswith("ro")))
    return Template(name=name, image=image, mappings=tuple(mappings))


def normalise_host_path(host: str) -> PurePosixPath | None:
    """The ``/mnt/user/<share>/...`` form of a host path, or None if it isn't in a share.

    On Unraid the same files appear under ``/mnt/user/<share>``, ``/mnt/user0/<share>``,
    ``/mnt/diskN/<share>`` and ``/mnt/<pool>/<share>`` (e.g. ``/mnt/cache``).
    """
    path = PurePosixPath(host)
    if not path.is_absolute() or ".." in path.parts:
        return None
    parts = path.parts  # ("/", "mnt", "<where>", "<share>", ...)
    if len(parts) < 4 or parts[1] != "mnt":
        return None
    where = parts[2]
    if where in ("user", "user0") or (where not in _NOT_POOLS):
        return USER_ROOT.joinpath(*parts[3:])
    return None


def share_of(path: PurePosixPath) -> str | None:
    """The share name of a normalised path."""
    try:
        return path.relative_to(USER_ROOT).parts[0]
    except (ValueError, IndexError):
        return None


def _looks_like_app_data(target: str) -> bool:
    """Container paths that usually hold an app's own settings and databases."""
    last = PurePosixPath(target).name.lower()
    return last in ("config", ".config", "appdata") or last.endswith("config")


def discover_app(template: Template, data_shares: frozenset[str]) -> App:
    """Which of the template's folders are app data on the mounted data shares."""
    folders: list[DataFolder] = []
    missing: list[MissingShare] = []
    for mapping in template.mappings:
        host = normalise_host_path(mapping.host)
        share = None if host is None else share_of(host)
        if host is None or share is None or host == USER_ROOT / share:
            continue  # not in a share, or a whole share (media, downloads...)
        if share in data_shares:
            folders.append(DataFolder(target=mapping.target, host=str(host), share=share))
        elif _looks_like_app_data(mapping.target):
            missing.append(MissingShare(target=mapping.target, host=str(host), share=share))
    return App(
        name=template.name, image=template.image, folders=tuple(folders), missing=tuple(missing)
    )


def mounted_data_shares(data_root: Path) -> frozenset[str]:
    """Shares mounted into BackupHaven under ``data_root`` (``/mnt/user`` in the container)."""
    try:
        return frozenset(p.name for p in data_root.iterdir() if p.is_dir())
    except OSError:
        return frozenset()


def discover(templates_dir: Path, data_root: Path) -> list[App]:
    """Every container template, matched against the mounted data shares."""
    shares = mounted_data_shares(data_root)
    apps: list[App] = []
    try:
        files = sorted(templates_dir.glob("*.xml"))
    except OSError:
        files = []
    for path in files:
        try:
            if path.stat().st_size > MAX_TEMPLATE_BYTES:
                raise TemplateError("template too large")
            template = parse_template(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, TemplateError) as exc:
            logger.warning("template skipped", extra={"file": path.name, "reason": str(exc)})
            continue
        apps.append(replace(discover_app(template, shares), template_file=str(path)))
    return sorted(apps, key=lambda app: app.name.lower())
