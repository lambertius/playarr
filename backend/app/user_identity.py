"""
User Identity — persistent anonymous user ID for edit provenance.

Generates a stable UUID per instance, stored in the settings table.
Used to attribute edits when data is shared with the musicvideo DB,
enabling per-user trust scoring without exposing personal information.
"""
import uuid
import logging
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_SETTING_KEY = "instance_user_id"
_cached_user_id: Optional[str] = None


def get_instance_user_id(db: Session) -> str:
    """Return the persistent anonymous user ID for this instance.

    Creates one on first call and caches it for the process lifetime.
    """
    global _cached_user_id
    if _cached_user_id is not None:
        return _cached_user_id

    from app.models import AppSetting

    row = (
        db.query(AppSetting)
        .filter(AppSetting.key == _SETTING_KEY, AppSetting.user_id.is_(None))
        .first()
    )
    if row:
        _cached_user_id = row.value
        return _cached_user_id

    # First launch — generate a new anonymous ID
    new_id = uuid.uuid4().hex
    setting = AppSetting(
        key=_SETTING_KEY,
        value=new_id,
        value_type="string",
    )
    db.add(setting)
    db.commit()
    _cached_user_id = new_id
    logger.info("Generated new instance user ID")
    return _cached_user_id
