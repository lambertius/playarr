"""
Preferences API — server-side storage for client UI preferences.

These are the settings that used to live in each browser's localStorage
(NowPlaying visualizer config, party-mode exclusions/animation, library
sort/view, per-page filters, …).  Storing them centrally means every browser
sees the same preferences and the Kodi add-on can read the shared ones too.

Storage reuses the ``app_settings`` table with a ``pref.`` key namespace and
``user_id = NULL`` (a single shared config for this self-hosted instance).
Every group is defined in the typed preference registry. Updates use field-level
PATCH and optimistic revisions so two clients cannot silently replace a group.
"""
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AppSetting
from app.services.preference_registry import (
    get_preference_definition,
    preference_catalogue,
)

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


@router.get("/state")
def get_preference_state(db: Session = Depends(get_db)) -> dict:
    """Return values and revisions used by optimistic PATCH clients."""
    rows = (
        db.query(AppSetting)
        .filter(AppSetting.key.like(PREFIX + "%"), AppSetting.user_id.is_(None))
        .all()
    )
    values: dict[str, Any] = {}
    revisions: dict[str, int] = {}
    for row in rows:
        name = row.key[len(PREFIX):]
        try:
            definition = get_preference_definition(name)
        except KeyError:
            continue
        try:
            current = json.loads(row.value) if row.value is not None else {}
        except (json.JSONDecodeError, TypeError):
            current = {}
        values[name] = definition.merged(current, {})
        revisions[name] = row.revision
    return {"values": values, "revisions": revisions}


@router.get("/registry")
def get_registry() -> dict:
    return preference_catalogue()


class PreferenceUpdate(BaseModel):
    value: Any = None


class PreferencePatch(BaseModel):
    patch: dict[str, Any] = Field(default_factory=dict)
    revision: int = Field(ge=0)


@router.patch("/{name}")
def patch_preference(name: str, body: PreferencePatch, db: Session = Depends(get_db)) -> dict:
    """Merge validated fields when the caller's revision matches."""
    try:
        definition = get_preference_definition(name)
        definition.validate_patch(body.patch)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    key = PREFIX + name
    row = (
        db.query(AppSetting)
        .filter(AppSetting.key == key, AppSetting.user_id.is_(None))
        .first()
    )
    current_revision = row.revision if row else 0
    if body.revision != current_revision:
        raise HTTPException(
            409,
            {"message": "Preference revision conflict", "current_revision": current_revision},
        )
    try:
        current = json.loads(row.value) if row and row.value is not None else {}
    except (json.JSONDecodeError, TypeError):
        current = {}
    value = definition.merged(current, body.patch)
    if row:
        row.value = json.dumps(value)
        row.value_type = "json"
        row.revision += 1
    else:
        row = AppSetting(
            user_id=None, key=key, value=json.dumps(value), value_type="json", revision=1,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return {"name": name, "value": value, "revision": row.revision, "schema_version": definition.schema_version}


@router.put("/{name}")
def set_preference(name: str, body: PreferenceUpdate, db: Session = Depends(get_db)) -> dict:
    """Compatibility endpoint for one release; validates known full group values."""
    try:
        definition = get_preference_definition(name)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not isinstance(body.value, dict):
        raise HTTPException(422, "Preference value must be an object")
    try:
        definition.validate_patch(body.value)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    key = PREFIX + name
    encoded = json.dumps(definition.merged({}, body.value))
    row = (
        db.query(AppSetting)
        .filter(AppSetting.key == key, AppSetting.user_id.is_(None))
        .first()
    )
    if row:
        row.value = encoded
        row.value_type = "json"
        row.revision += 1
    else:
        row = AppSetting(user_id=None, key=key, value=encoded, value_type="json", revision=1)
        db.add(row)
    db.commit()
    db.refresh(row)
    return {"name": name, "value": json.loads(encoded), "revision": row.revision}
