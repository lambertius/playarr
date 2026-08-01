"""Acceptance coverage for durable, pairwise, evidence-hashed review cases."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models import ReviewCase, VideoItem
from app.routers.resolve import (
    ReviewCaseDismissRequest,
    ReviewCasePlanRequest,
    commit_review_case_plan,
    dismiss_review_case,
    stage_review_case_plan,
)
from app.services.review_cases import sync_review_cases


def _session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _duplicate_cluster(db):
    videos = []
    for index in range(3):
        video = VideoItem(
            artist="Test Artist",
            title="Same Song",
            file_path=f"C:/library/copy-{index}.mkv",
            file_checksum=f"checksum-{index}",
            review_status="needs_human_review",
            review_category="duplicate",
            review_reason="Generated duplicate evidence",
        )
        db.add(video)
        videos.append(video)
    db.commit()
    return videos


def test_three_video_cluster_materializes_all_three_pair_edges():
    db = _session()
    videos = _duplicate_cluster(db)

    cases = sync_review_cases(db)
    db.commit()

    assert len(cases) == 1
    case = cases[0]
    assert case.category == "duplicate"
    assert len(case.items) == 3
    assert len(case.edges) == 3
    pairs = {
        (edge.left_video_stable_id, edge.right_video_stable_id)
        for edge in case.edges
    }
    assert len(pairs) == 3
    assert {item.video_stable_id for item in case.items} == {video.stable_id for video in videos}


def test_dismissal_survives_identical_scan_and_reopens_for_new_evidence():
    db = _session()
    videos = _duplicate_cluster(db)
    case = sync_review_cases(db)[0]
    db.commit()

    dismissed = dismiss_review_case(
        case.stable_id,
        ReviewCaseDismissRequest(expected_revision=case.revision),
        db,
    )
    assert dismissed["status"] == "dismissed"
    dismissed_revision = dismissed["revision"]

    sync_review_cases(db)
    db.commit()
    unchanged = db.get(ReviewCase, case.id)
    assert unchanged.status == "dismissed"
    assert unchanged.revision == dismissed_revision

    videos[0].file_checksum = "materially-new-checksum"
    db.commit()
    sync_review_cases(db)
    db.commit()
    reopened = db.get(ReviewCase, case.id)
    assert reopened.status == "open"
    assert reopened.revision == dismissed_revision + 1


def test_staged_reclassification_commits_as_one_case_revision():
    db = _session()
    videos = _duplicate_cluster(db)
    case = sync_review_cases(db)[0]
    db.commit()

    staged = stage_review_case_plan(
        case.stable_id,
        ReviewCasePlanRequest(
            expected_revision=case.revision,
            actions=[
                {
                    "type": "reclassify",
                    "video_stable_id": videos[0].stable_id,
                    "version_type": "live",
                },
                {"type": "keep"},
            ],
        ),
        db,
    )
    assert staged["consequences"]["metadata"][0]["new_value"] == "live"

    committed = commit_review_case_plan(case.stable_id, staged["plan_id"], db)
    assert committed["case"]["status"] == "resolved"
    assert committed["case"]["revision"] == 2
    assert db.get(VideoItem, videos[0].id).version_type == "live"
    assert all(video.review_status == "none" for video in db.query(VideoItem).all())
