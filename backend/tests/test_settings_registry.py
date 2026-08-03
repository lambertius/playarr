import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.routers.settings import DEFAULT_SETTINGS, SETTING_CONSUMERS, settings_catalogue, update_setting
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
    assert set(SETTING_CONSUMERS) == set(DEFAULT_SETTINGS)


def test_startup_rename_scan_is_a_registered_setting():
    rows = {row["key"]: row for row in settings_catalogue(_session())["definitions"]}
    assert rows["startup_rename_scan"]["default"] == "false"
    assert rows["startup_rename_scan"]["consumers"] == ["startup.review_scans"]


def test_catalogue_distinguishes_external_deprecated_and_unknown_database_keys():
    db = _session()
    db.add_all([
        app.models.AppSetting(key="ai_system_prompt", value="custom", value_type="string"),
        app.models.AppSetting(key="party_mode_exclusions", value="{}", value_type="json"),
        app.models.AppSetting(key="unregistered_test_key", value="x", value_type="string"),
    ])
    db.commit()
    audit = settings_catalogue(db)["audit"]
    assert "ai_system_prompt" in audit["externally_managed_keys"]
    assert audit["deprecated_database_keys"] == ["party_mode_exclusions"]
    assert audit["orphaned_database_keys"] == ["unregistered_test_key"]


def test_config_backed_values_are_applied_immediately():
    from app.config import get_settings

    config = get_settings()
    original = config.preview_duration_sec
    try:
        update_setting(SettingUpdate(key="preview_duration_sec", value="17", value_type="int"), db=_session())
        assert config.preview_duration_sec == 17
    finally:
        config.preview_duration_sec = original


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
