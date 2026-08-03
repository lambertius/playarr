"""Acceptance coverage for durable, pairwise, evidence-hashed review cases."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models import JobStatus, ProcessingJob, QualitySignature, ReviewCase, VideoItem
from app.routers.resolve import (
    ReviewCaseDismissRequest,
    ReviewCasePlanRequest,
    commit_review_case_plan,
    dismiss_review_case,
    list_review_cases,
    scan_review_health,
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


def test_three_video_cluster_materializes_three_independent_pair_cases():
    db = _session()
    videos = _duplicate_cluster(db)

    cases = sync_review_cases(db)
    db.commit()

    assert len(cases) == 3
    assert all(case.category == "duplicate" for case in cases)
    assert all(len(case.items) == 2 for case in cases)
    assert all(len(case.edges) == 1 for case in cases)
    pairs = {
        (edge.left_video_stable_id, edge.right_video_stable_id)
        for case in cases
        for edge in case.edges
    }
    assert len(pairs) == 3
    assert set().union(*(
        {item.video_stable_id for item in case.items} for case in cases
    )) == {video.stable_id for video in videos}


def test_non_normal_legacy_review_uses_version_classification_case():
    db = _session()
    db.add(VideoItem(
        artist="Cover Artist", title="Song", version_type="cover",
        review_status="needs_human_review", review_reason="Possible cover version",
    ))
    db.commit()
    case = sync_review_cases(db)[0]
    assert case.category == "version_detection"


def test_completeness_rule_materializes_filterable_enrichment_evidence():
    db = _session()
    db.add(VideoItem(artist="Artist", title="Incomplete", processing_state={}))
    db.commit()
    cases = sync_review_cases(db, include_enrichment_completeness=True)
    case = next(item for item in cases if item.category == "enrichment_incomplete")
    missing = case.items[0].evidence_summary_json["missing_enrichment"]
    assert {"no_ai", "no_thumbnails", "no_scene_analysis", "no_wikipedia", "no_mbid"} <= set(missing)


def test_dismissal_survives_identical_scan_and_reopens_for_new_evidence():
    db = _session()
    videos = _duplicate_cluster(db)
    case = next(
        item for item in sync_review_cases(db)
        if videos[0].stable_id in {case_item.video_stable_id for case_item in item.items}
    )
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


def test_dismissing_one_pair_leaves_other_cluster_pairs_open():
    db = _session()
    videos = _duplicate_cluster(db)
    cases = sync_review_cases(db)
    db.commit()

    dismissed_case = cases[0]
    dismissed_ids = {item.video_stable_id for item in dismissed_case.items}
    dismiss_review_case(
        dismissed_case.stable_id,
        ReviewCaseDismissRequest(expected_revision=dismissed_case.revision),
        db,
    )

    assert db.query(ReviewCase).filter(ReviewCase.status == "open").count() == 2
    assert all(video.review_category == "duplicate" for video in videos)
    pair_videos = [video for video in videos if video.stable_id in dismissed_ids]
    assert pair_videos[1].id in pair_videos[0].dismissed_duplicate_ids
    assert pair_videos[0].id in pair_videos[1].dismissed_duplicate_ids


def test_staged_reclassification_commits_as_one_case_revision():
    db = _session()
    videos = _duplicate_cluster(db)
    case = next(
        item for item in sync_review_cases(db)
        if videos[0].stable_id in {case_item.video_stable_id for case_item in item.items}
    )
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
    assert committed["case"]["revision"] > 1
    assert db.get(VideoItem, videos[0].id).version_type == "live"
    # Other pair cases remain open, so their videos retain transition flags.
    assert db.query(ReviewCase).filter(ReviewCase.status == "open").count() == 2


def test_deleting_one_candidate_clears_only_affected_pair_cases():
    db = _session()
    videos = _duplicate_cluster(db)
    cases = sync_review_cases(db)
    db.commit()
    target = videos[0]
    case = next(
        item for item in cases
        if target.stable_id in {case_item.video_stable_id for case_item in item.items}
    )
    staged = stage_review_case_plan(
        case.stable_id,
        ReviewCasePlanRequest(expected_revision=case.revision, actions=[
            {"type": "delete", "video_stable_id": target.stable_id},
            {"type": "keep"},
        ]),
        db,
    )

    commit_review_case_plan(case.stable_id, staged["plan_id"], db)

    assert db.get(VideoItem, target.id) is None
    assert db.query(ReviewCase).filter(
        ReviewCase.category == "duplicate", ReviewCase.status == "open",
    ).count() == 1


def test_deleting_candidate_with_files_archives_and_commits(tmp_path, monkeypatch):
    """The real review workflow must survive the journal's checkpoint commits."""
    from app.config import get_settings

    library = tmp_path / "library"
    target_folder = library / "Artist" / "Target"
    partner_folder = library / "Artist" / "Partner"
    target_folder.mkdir(parents=True)
    partner_folder.mkdir(parents=True)
    target_file = target_folder / "target.mkv"
    target_sidecar = target_folder / "target.playarr.xml"
    partner_file = partner_folder / "partner.mkv"
    target_file.write_bytes(b"target video")
    target_sidecar.write_text("<playarr/>", encoding="utf-8")
    partner_file.write_bytes(b"partner video")
    monkeypatch.setattr(get_settings(), "library_dir", str(library))

    db = _session()
    db.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
    target = VideoItem(
        artist="Test Artist", title="Same Song", file_path=str(target_file),
        folder_path=str(target_folder), review_status="needs_human_review",
        review_category="duplicate",
    )
    partner = VideoItem(
        artist="Test Artist", title="Same Song", file_path=str(partner_file),
        folder_path=str(partner_folder), review_status="needs_human_review",
        review_category="duplicate",
    )
    db.add_all([target, partner])
    db.commit()
    target_id = target.id
    target_stable_id = target.stable_id
    case = sync_review_cases(db)[0]
    db.commit()

    staged = stage_review_case_plan(
        case.stable_id,
        ReviewCasePlanRequest(expected_revision=case.revision, actions=[
            {"type": "delete", "video_stable_id": target_stable_id},
            {"type": "keep"},
        ]),
        db,
    )
    committed = commit_review_case_plan(case.stable_id, staged["plan_id"], db)

    assert committed["status"] == "committed"
    assert db.get(VideoItem, target_id) is None
    assert not target_file.exists()
    archived = library / "_archive" / "review-delete" / target_stable_id
    assert any(path.name == target_file.name for path in archived.rglob("*"))
    assert any(path.name == target_sidecar.name for path in archived.rglob("*"))


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
    partner = VideoItem(
        artist="Plan", title="Actions", file_path=str(tmp_path / "partner.mkv"),
        folder_path=str(tmp_path / "partner"), review_status="needs_human_review",
        review_category="duplicate",
    )
    db.add_all([video, partner]); db.commit()
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
        "version_ambiguity", "low_certainty_import",
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


def test_health_scan_flags_normalization_drift_and_requested_steps():
    db = _session()
    drift = VideoItem(
        artist="Loud", title="Drift", file_path="C:/library/drift.mkv",
        processing_state={"audio_normalized": {"completed": True}},
    )
    drift.quality_signature = QualitySignature(loudness_lufs=-10.0)
    incomplete = VideoItem(
        artist="Missing", title="AI", file_path="C:/library/missing.mkv",
        processing_state={},
    )
    db.add_all([drift, incomplete]); db.flush()
    db.add(ProcessingJob(
        video_id=incomplete.id, job_type="import_url", status=JobStatus.complete,
        display_name="Missing - AI",
        input_params={"ai_auto_analyse": True, "scene_analysis": True},
    ))
    db.commit()

    result = scan_review_health(rescan_all=False, db=db)

    assert result["counts"]["normalization_mismatch"] == 1
    assert result["counts"]["requested_step_incomplete"] == 1
    assert drift.review_category == "normalization_mismatch"
    assert "current target" in drift.review_reason
    assert incomplete.review_category == "requested_step_incomplete"
    assert "AI enrichment" in incomplete.review_reason
