"""
Preferences API — server-side storage for client UI preferences.

These are the settings that used to live in each browser's localStorage
(NowPlaying visualizer config, party-mode exclusions/animation, library
sort/view, per-page filters, …).  Storing them centrally means every browser
sees the same preferences and the Kodi add-on can read the shared ones too.

Storage reuses the ``app_settings`` table with a ``pref.`` key namespace and
``user_id = NULL`` (a single shared config for this self-hosted instance).
Each preference "group" holds an arbitrary JSON value — the server treats it
as an opaque blob; clients own the schema of each group.
"""
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AppSetting

router = APIRouter(prefix="/api/preferences", tags=["Preferences"])
logger = logging.getLogger(__name__)

PREFIX = "pref."


def get_preference(db: Session, name: str, default: Any = None) -> Any:
    """Read and JSON-decode a single preference group. Returns *default* if unset.

    Intended for internal use by other routers that need to honour a shared
    preference server-side (e.g. party-mode exclusions, default library sort).
    """
    row = (
        db.query(AppSetting)
        .filter(AppSetting.key == PREFIX + name, AppSetting.user_id.is_(None))
        .first()
    )
    if not row or row.value is None:
        return default
    try:
        return json.loads(row.value)
    except (json.JSONDecodeError, TypeError):
        return default


@router.get("")
@router.get("/")
def get_preferences(db: Session = Depends(get_db)) -> dict:
    """Return every stored preference group as ``{name: value}``."""
    rows = (
        db.query(AppSetting)
        .filter(AppSetting.key.like(PREFIX + "%"), AppSetting.user_id.is_(None))
        .all()
    )
    out: dict[str, Any] = {}
    for r in rows:
        name = r.key[len(PREFIX):]
        try:
            out[name] = json.loads(r.value) if r.value is not None else None
        except (json.JSONDecodeError, TypeError):
            out[name] = r.value
    return out


class PreferenceUpdate(BaseModel):
    value: Any = None


@router.put("/{name}")
def set_preference(name: str, body: PreferenceUpdate, db: Session = Depends(get_db)) -> dict:
    """Create or replace a single preference group with an arbitrary JSON value."""
    key = PREFIX + name
    encoded = json.dumps(body.value)
    row = (
        db.query(AppSetting)
        .filter(AppSetting.key == key, AppSetting.user_id.is_(None))
        .first()
    )
    if row:
        row.value = encoded
        row.value_type = "json"
    else:
        db.add(AppSetting(user_id=None, key=key, value=encoded, value_type="json"))
    db.commit()
    return {"name": name, "value": body.value}
