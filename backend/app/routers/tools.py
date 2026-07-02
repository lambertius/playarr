"""
Tools API — manage the external CLI tools Playarr depends on (currently yt-dlp).

Lets an installed app keep yt-dlp current without a full reinstall.
"""
import logging

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import ytdlp_updater

router = APIRouter(prefix="/api/tools", tags=["Tools"])
logger = logging.getLogger(__name__)


class YtdlpStatus(BaseModel):
    installed_version: str | None = None
    latest_version: str | None = None
    update_available: bool = False
    managed: bool = False
    path: str | None = None
    managed_path: str | None = None


class YtdlpUpdateResult(BaseModel):
    success: bool
    message: str
    installed_version: str | None = None
    latest_version: str | None = None
    update_available: bool = False
    managed: bool = False
    path: str | None = None
    managed_path: str | None = None


@router.get("/ytdlp", response_model=YtdlpStatus)
def ytdlp_status():
    """Current yt-dlp version, the latest available, and whether it's managed."""
    return ytdlp_updater.get_status()


@router.post("/ytdlp/update", response_model=YtdlpUpdateResult)
def ytdlp_update():
    """Download the latest yt-dlp into the managed tools dir and use it."""
    success, message, _new_version = ytdlp_updater.update()
    status = ytdlp_updater.get_status()
    return {"success": success, "message": message, **status}
