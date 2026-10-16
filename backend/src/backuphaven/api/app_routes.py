"""Apps found from the Unraid templates (ARCHITECTURE.md §6.1)."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from backuphaven import discovery
from backuphaven.api.deps import InteractiveDep, csrf_protect, require_setup_done
from backuphaven.config import Settings

router = APIRouter(dependencies=[Depends(require_setup_done), Depends(csrf_protect)])


class DataFolderView(BaseModel):
    target: str
    host: str
    share: str


class AppView(BaseModel):
    name: str
    image: str
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
    return DiscoveryView(
        data_shares=sorted(discovery.mounted_data_shares(settings.data_root)),
        templates_found=settings.templates_dir.is_dir(),
        apps=[
            AppView(
                name=app.name,
                image=app.image,
                folders=[DataFolderView(**vars(f)) for f in app.folders],
                missing=[DataFolderView(**vars(m)) for m in app.missing],
            )
            for app in apps
        ],
    )
