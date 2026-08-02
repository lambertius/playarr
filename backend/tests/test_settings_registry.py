import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.routers.settings import settings_catalogue, update_setting
from app.schemas import SettingUpdate


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_catalogue_has_complete_definition_and_consumer_audit():
    catalogue = settings_catalogue(_session())
    required = {"key", "value_type", "default", "group", "scope", "constraints", "restart_required", "secret", "dependencies", "deprecated", "consumers"}
    assert catalogue["definitions"]
    assert all(required <= set(row) for row in catalogue["definitions"])
    assert catalogue["audit"]["visible_without_consumers"] == []


def test_tmvdb_children_cannot_enable_without_parent_and_secret():
    db = _session()
    with pytest.raises(HTTPException) as exc:
        update_setting(SettingUpdate(key="tmvdb_auto_pull", value="true", value_type="bool"), db=db)
    assert exc.value.status_code == 422


def test_secret_is_never_returned_after_submit():
    db = _session()
    result = update_setting(SettingUpdate(key="tmvdb_api_key", value="abcdefgh-secret", value_type="string"), db=db)
    assert result.value.endswith("cret")
    assert "abcdefgh" not in result.value
