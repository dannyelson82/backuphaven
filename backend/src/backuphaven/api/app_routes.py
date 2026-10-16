"""Apps found from the Unraid templates (ARCHITECTURE.md §6.1)."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from backuphaven import discovery
from backuphaven.api.deps import InteractiveDep, csrf_protect, require_setup_done
from backuphaven.config import Settings
from backuphaven.plugins import registry

router = APIRouter(dependencies=[Depends(require_setup_done), Depends(csrf_protect)])


class DataFolderView(BaseModel):
    target: str
    host: str
    share: str


class AppView(BaseModel):
    name: str
    image: str
    plugin: str
    folders: list[DataFolderView]
    missing: list[DataFolderView]


class DiscoveryView(BaseModel):
    data_shares: list[str]
    templates_found: bool
    apps: list[AppView]


@router.get("/apps/discovered")
def discovered(request: Request, _principal: InteractiveDep) -> DiscoveryView:
    settings: Settings = request.app.state.settings
    apps = discovery.discover(settings.templates_dir, settings.data_root)
    known = registry.bundled()
    return DiscoveryView(
        data_shares=sorted(discovery.mounted_data_shares(settings.data_root)),
        templates_found=settings.templates_dir.is_dir(),
        apps=[
            AppView(
                name=app.name,
                image=app.image,
                plugin=known.match(app.image).id,
                folders=[DataFolderView(**vars(f)) for f in app.folders],
                missing=[DataFolderView(**vars(m)) for m in app.missing],
            )
            for app in apps
        ],
    )


class SettingView(BaseModel):
    id: str
    label: str
    help: str
    default: bool


class PluginView(BaseModel):
    id: str
    name: str
    version: str
    description: str
    mode: str
    settings: list[SettingView]


@router.get("/plugins")
def plugins(_principal: InteractiveDep) -> list[PluginView]:
    return [
        PluginView(
            id=m.id,
            name=m.name,
            version=m.version,
            description=m.description,
            mode=m.backup.mode,
            settings=[
                SettingView(id=s.id, label=s.label, help=s.help, default=s.default)
                for s in m.settings
            ],
        )
        for m in registry.bundled().plugins.values()
    ]
