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
    list_review_cases,
    stage_review_case_plan,
)
from app.services.review_cases import sync_orphan_review_cases, sync_review_cases


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


def test_case_listing_is_sql_paginated():
    db = _session()
    for index in range(25):
        db.add(VideoItem(
            artist=f"Artist {index}", title="Review", file_path=f"C:/library/{index}.mkv",
            review_status="needs_human_review", review_category="low_certainty_import",
        ))
    db.commit()

    page = list_review_cases(status="open", category=None, page=2, page_size=10, db=db)
    assert page["total"] == 25
    assert page["page"] == 2
    assert page["total_pages"] == 3
    assert len(page["items"]) == 10


def test_plan_previews_rescrape_normalise_and_recoverable_delete(tmp_path):
    db = _session()
    source = tmp_path / "video.mkv"
    sidecar = tmp_path / "video.playarr.xml"
    source.write_bytes(b"video")
    sidecar.write_text("<playarr/>", encoding="utf-8")
    video = VideoItem(
        artist="Plan", title="Actions", file_path=str(source), folder_path=str(tmp_path),
        review_status="needs_human_review", review_category="duplicate",
    )
    db.add(video); db.commit()
    case = sync_review_cases(db)[0]; db.commit()

    staged = stage_review_case_plan(
        case.stable_id,
        ReviewCasePlanRequest(expected_revision=case.revision, actions=[
            {"type": "rescrape", "video_stable_id": video.stable_id},
            {"type": "normalise", "video_stable_id": video.stable_id},
            {"type": "delete", "video_stable_id": video.stable_id},
            {"type": "keep"},
        ]),
        db,
    )
    assert {job["job_type"] for job in staged["consequences"]["jobs"]} == {"metadata_scrape", "normalize"}
    assert staged["consequences"]["files"][0]["recoverable"] is True
    assert set(staged["consequences"]["files"][0]["paths"]) == {str(source), str(sidecar)}


def test_all_structured_review_categories_do_not_parse_reason_text():
    db = _session()
    categories = (
        "duplicate", "version_ambiguity", "low_certainty_import",
        "requested_step_incomplete", "normalization_failure",
        "rename", "canonical_link_mismatch",
    )
    for index, category in enumerate(categories):
        db.add(VideoItem(
            artist=f"Artist {index}", title=f"Title {index}",
            review_status="needs_human_review", review_category=category,
            review_reason="identical unstructured text",
        ))
    db.commit()
    cases = sync_review_cases(db)
    assert {case.category for case in cases} == set(categories)
    assert all(case.trigger_code == f"video_flag:{case.category}" for case in cases)


def test_orphan_files_materialize_without_a_video_row():
    db = _session()
    cases = sync_orphan_review_cases(db, [{
        "folder_path": "C:/library/untracked", "size_bytes": 42,
        "file_count": 1, "files": ["orphan.mkv"],
    }])
    db.commit()
    assert len(cases) == 1
    assert cases[0].category == "orphan_file"
    assert cases[0].items == []
    assert cases[0].evidence_json["trigger_code"] == "filesystem_scan:unrepresented_media"
