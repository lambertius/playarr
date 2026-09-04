import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.routers.preferences import PreferencePatch, patch_preference


def _db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_different_field_patches_rebase_without_replacing_group():
    db = _db()
    first = patch_preference(
        "library", PreferencePatch(patch={"view": "list"}, revision=0), db,
    )
    second = patch_preference(
        "library", PreferencePatch(patch={"pageSize": 96}, revision=first["revision"]), db,
    )

    assert second["value"]["view"] == "list"
    assert second["value"]["pageSize"] == 96
    assert second["revision"] == 2


def test_stale_revision_returns_conflict():
    db = _db()
    patch_preference("panels", PreferencePatch(patch={"thumbnailsExpanded": True}, revision=0), db)

    with pytest.raises(HTTPException) as caught:
        patch_preference(
            "panels", PreferencePatch(patch={"trackHistoryExpanded": True}, revision=0), db,
        )

    assert caught.value.status_code == 409
    assert caught.value.detail["current_revision"] == 1


def test_unknown_or_invalid_fields_are_rejected():
    db = _db()
    with pytest.raises(HTTPException) as unknown:
        patch_preference("library", PreferencePatch(patch={"mystery": True}, revision=0), db)
    assert unknown.value.status_code == 422

    with pytest.raises(HTTPException) as invalid:
        patch_preference("library", PreferencePatch(patch={"view": "cards"}, revision=0), db)
    assert invalid.value.status_code == 422


def test_library_page_size_accepts_all():
    db = _db()
    result = patch_preference(
        "library", PreferencePatch(patch={"pageSize": 0}, revision=0), db,
    )

    assert result["value"]["pageSize"] == 0
