"""SIDE-005/SIDE-006 empty-database rebuild acceptance."""
from pathlib import Path
from types import SimpleNamespace
from xml.etree.ElementTree import tostring

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.ai.models  # noqa: F401
import app.models  # noqa: F401
from app.ai.models import AIMetadataResult, AIProvider, AIResultStatus, AISceneAnalysis, AIThumbnail, SceneAnalysisStatus
from app.database import Base
from app.models import (
    ArtistConsolidation,
    ArtistConsolidationTarget,
    FieldProvenanceEvent,
    ContributionLog,
    MetadataSnapshot,
    Playlist,
    PlaylistEntry,
    QualitySignature,
    ReviewCase,
    ReviewCaseEdge,
    ReviewCaseItem,
    VideoItem,
    VideoEditorQueueEntry,
)
from app.services.consolidations import library_consolidation_manifest, library_manifest_hash, load_library_manifest
from app.services.playarr_xml import build_playarr_xml
from app.services.sidecar_restore import field_coverage, parse_restore_document, rebuild_from_sidecars


def _sessions():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _projection(db):
    rows = db.query(VideoItem).filter(VideoItem.playarr_video_id.isnot(None)).all()
    by_id = {row.id: row.playarr_video_id for row in rows}
    return sorted((
        row.playarr_video_id,
        row.playarr_track_id,
        row.artist,
        row.title,
        by_id.get(row.parent_video_id),
        tuple(sorted(by_id[value] for value in (row.dismissed_duplicate_ids or []))),
        tuple(sorted(
            (by_id.get(value.get("video_id")), value.get("label"))
            for value in (row.related_versions or [])
        )),
        tuple(sorted(genre.name for genre in row.genres)),
        row.quality_signature.width if row.quality_signature else None,
    ) for row in rows)


def test_two_pass_rebuild_preserves_logical_state_with_different_row_ids(tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(library_dir=str(library), archive_dir=str(tmp_path / "archive")),
    )
    source_sessions = _sessions()
    source = source_sessions()
    parent_path = library / "Parent.mp4"
    child_path = library / "Child.mp4"
    parent_path.write_bytes(b"parent-fixture")
    child_path.write_bytes(b"child-fixture")
    parent = VideoItem(
        stable_id="11111111-1111-4111-8111-111111111111",
        playarr_video_id="PVD-parent", playarr_track_id="PTR-track",
        artist="Fixture Artist", title="Fixture Track", file_path=str(parent_path),
        folder_path=str(library), genres=[], revision=3,
    )
    child = VideoItem(
        stable_id="22222222-2222-4222-8222-222222222222",
        playarr_video_id="PVD-child", playarr_track_id="PTR-track",
        artist="Fixture Artist", title="Fixture Track (Live)", file_path=str(child_path),
        folder_path=str(library), version_type="live", genres=[], revision=4,
    )
    source.add_all([parent, child])
    source.flush()
    child.parent_video_id = parent.id
    parent.dismissed_duplicate_ids = [child.id]
    parent.related_versions = [{"video_id": child.id, "label": "Live"}]
    from app.pipeline_lib.db_apply import _get_or_create_genre
    parent.genres.append(_get_or_create_genre(source, "Rock"))
    source.add(QualitySignature(video_id=parent.id, width=1920, height=1080))
    source.add(ArtistConsolidation(
        stable_id="33333333-3333-4333-8333-333333333333", mask_name="Fixture Artist",
        targets=[ArtistConsolidationTarget(raw_name="Fixture Artist", provenance="test")],
    ))
    source.add(Playlist(
        stable_id="44444444-4444-4444-8444-444444444444", name="Portable set",
        revision=7, entries=[
            PlaylistEntry(occurrence_id="55555555-5555-4555-8555-555555555555", video_id=parent.id, position=0),
            PlaylistEntry(occurrence_id="66666666-6666-4666-8666-666666666666", video_id=child.id, position=1),
        ],
    ))
    review = ReviewCase(
        stable_id="77777777-7777-4777-8777-777777777777", category="duplicate",
        status="open", revision=2, trigger_code="fixture_duplicate",
        evidence_hash="fixture-evidence", evidence_json={"reason": "same track"},
    )
    review.items.extend([
        ReviewCaseItem(video_id=parent.id, video_stable_id=parent.stable_id, role="left", evidence_summary_json={}),
        ReviewCaseItem(video_id=child.id, video_stable_id=child.stable_id, role="right", evidence_summary_json={}),
    ])
    review.edges.append(ReviewCaseEdge(
        left_video_stable_id=parent.stable_id, right_video_stable_id=child.stable_id,
        evidence_type="fixture", score=1.0, evidence_hash="edge-fixture", evidence_json={},
    ))
    source.add(review)
    source.add(FieldProvenanceEvent(
        id="88888888-8888-4888-8888-888888888888", video_id=parent.id,
        video_stable_id=parent.stable_id, field_name="title", event_type="field_changed",
        actor_kind="user", actor_id="fixture-user", provider="manual",
        prior_value_hash="a" * 64, resulting_value_hash="b" * 64,
        operation_id="op_fixture",
    ))
    source.commit()

    sidecars = []
    for video, path in ((parent, parent_path), (child, child_path)):
        root = build_playarr_xml(video, source)
        sidecar = path.with_suffix(".playarr.xml")
        sidecar.write_bytes(tostring(root, encoding="utf-8", xml_declaration=True))
        sidecars.append(sidecar)
    expected = _projection(source)
    import json
    (library / ".playarr-library-manifest.json").write_text(
        json.dumps(library_consolidation_manifest(source)), encoding="utf-8",
    )

    target_sessions = _sessions()
    target = target_sessions()
    target.add(VideoItem(artist="Unrelated", title="Consumes row one"))
    target.commit()
    report = rebuild_from_sidecars(target, reversed(sidecars), library_root=library)
    target.commit()

    assert report["counts"] == {
        "restored": 2, "migrated": 0, "ambiguous": 0, "missing": 0, "rejected": 0,
    }
    assert _projection(target) == expected
    assert target.query(ArtistConsolidation).filter(
        ArtistConsolidation.stable_id == "33333333-3333-4333-8333-333333333333",
    ).one().mask_name == "Fixture Artist"
    assert target.query(VideoItem).filter(VideoItem.playarr_video_id == "PVD-parent").one().id != parent.id
    restored_playlist = target.query(Playlist).filter(Playlist.stable_id == "44444444-4444-4444-8444-444444444444").one()
    assert restored_playlist.revision == 7
    assert [entry.video_item.playarr_video_id for entry in restored_playlist.entries] == ["PVD-parent", "PVD-child"]
    restored_review = target.query(ReviewCase).filter(ReviewCase.stable_id == "77777777-7777-4777-8777-777777777777").one()
    assert len(restored_review.items) == 2 and len(restored_review.edges) == 1
    restored_event = target.get(FieldProvenanceEvent, "88888888-8888-4888-8888-888888888888")
    assert restored_event and restored_event.operation_id == "op_fixture"


def test_canonical_parser_classifies_every_sidecar_field(tmp_path):
    path = tmp_path / "track.playarr.xml"
    from xml.etree.ElementTree import fromstring, tostring
    from app.services.sidecar_store import sidecar_root_hash
    root = fromstring("""<playarr version="2" schemaVersion="2" playarrVersion="test" sidecarRevision="1"
 entityRevision="1" generatedAt="2026-08-01T00:00:00Z" contentHash="sha256:test"
 entityId="44444444-4444-4444-8444-444444444444" playarrVideoId="PVD-test">
 <portable_identity entityId="44444444-4444-4444-8444-444444444444" videoId="PVD-test" trackId="PTR-test" />
 <identity><artist>Artist</artist><title>Track</title></identity><file><relative_path>Artist/Track.mp4</relative_path></file>
</playarr>""")
    root.set("contentHash", sidecar_root_hash(root))
    path.write_bytes(tostring(root, encoding="utf-8", xml_declaration=True))
    parsed = parse_restore_document(path)
    parsed.pop("_source_path")
    assert field_coverage(parsed)["unclassified"] == []


def test_rebuild_restores_ai_provenance_history_and_editor_draft(tmp_path):
    library = tmp_path / "moved-library"
    library.mkdir()
    media = library / "Portable.mp4"
    media.write_bytes(b"portable-state-media")
    thumb = library / "Portable-thumb.jpg"
    thumb.write_bytes(b"thumbnail")

    source = _sessions()()
    video = VideoItem(
        stable_id="99999999-9999-4999-8999-999999999999",
        playarr_video_id="PVD-portable", playarr_track_id="PTR-portable",
        artist="Portable Artist", title="Portable Track",
        folder_path=str(library), file_path=str(media), genres=[],
        field_provenance_users={"title": "instance-user"},
        last_edited_by="instance-user",
    )
    source.add(video); source.flush()
    source.add(AIMetadataResult(
        video_id=video.id, provider=AIProvider.openai, model_name="fixture-model",
        status=AIResultStatus.accepted, requested_fields=["title"],
        accepted_fields=["title"], prompt_used="fixture prompt", tokens_used=42,
    ))
    scene = AISceneAnalysis(
        video_id=video.id, status=SceneAnalysisStatus.complete,
        scenes=[{"start": 0, "end": 5}], config={"threshold": 0.3}, total_scenes=1,
    )
    source.add(scene); source.flush()
    source.add(AIThumbnail(
        video_id=video.id, scene_analysis_id=scene.id, timestamp_sec=2.5,
        file_path=str(thumb), score_overall=0.9, is_selected=True,
    ))
    source.add(MetadataSnapshot(video_id=video.id, snapshot_data={"title": "Before"}, reason="manual_edit"))
    source.add(ContributionLog(
        video_id=video.id, operation="push", status="submitted",
        playarr_video_id=video.playarr_video_id, payload_hash="a" * 64,
    ))
    source.add(VideoEditorQueueEntry(
        video_id=video.id, occurrence_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        source="manual", settings_json={"crf": 17}, position=3, revision=2,
    ))
    source.commit()

    sidecar = library / "Portable.playarr.xml"
    sidecar.write_bytes(tostring(build_playarr_xml(video, source), encoding="utf-8", xml_declaration=True))
    target = _sessions()()
    report = rebuild_from_sidecars(target, [sidecar], library_root=library)
    target.commit()

    restored = target.query(VideoItem).filter(VideoItem.playarr_video_id == "PVD-portable").one()
    ai = target.query(AIMetadataResult).filter(AIMetadataResult.video_id == restored.id).one()
    restored_scene = target.query(AISceneAnalysis).filter(AISceneAnalysis.video_id == restored.id).one()
    assert report["counts"]["restored"] == 1
    assert ai.status == AIResultStatus.accepted and ai.tokens_used == 42
    assert restored_scene.scenes == [{"start": 0, "end": 5}]
    assert target.query(AIThumbnail).filter(AIThumbnail.video_id == restored.id).one().is_selected is True
    assert target.query(MetadataSnapshot).filter(MetadataSnapshot.video_id == restored.id).one().reason == "manual_edit"
    assert target.query(ContributionLog).filter(ContributionLog.video_id == restored.id).one().status == "submitted"
    assert target.query(VideoEditorQueueEntry).filter(VideoEditorQueueEntry.video_id == restored.id).one().settings_json == {"crf": 17}
    assert restored.field_provenance_users == {"title": "instance-user"}


def test_rebuild_rejects_sidecar_when_media_checksum_no_longer_matches(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    media = library / "Checksum.mp4"
    media.write_bytes(b"original media")
    from app.services.content_id import compute_file_signature

    source = _sessions()()
    video = VideoItem(
        artist="Checksum Artist", title="Checksum Track", genres=[],
        folder_path=str(library), file_path=str(media),
        file_size_bytes=media.stat().st_size,
        file_checksum=compute_file_signature(str(media)),
    )
    source.add(video); source.commit()
    sidecar = library / "Checksum.playarr.xml"
    sidecar.write_bytes(tostring(build_playarr_xml(video, source), encoding="utf-8", xml_declaration=True))
    media.write_bytes(b"tampered media!")

    target = _sessions()()
    report = rebuild_from_sidecars(target, [sidecar], library_root=library)
    assert report["counts"]["restored"] == 0
    assert report["counts"]["rejected"] == 1
    assert "checksum mismatch" in report["rejected"][0]["reason"]


def test_library_manifest_recovers_from_valid_hashed_backup(tmp_path):
    import json
    primary = tmp_path / ".playarr-library-manifest.json"
    manifest = {
        "schema_version": 2,
        "artist_consolidations": [], "genre_consolidations": [],
        "playlists": [], "review_cases": [], "archive_operations": [],
    }
    manifest["content_hash"] = library_manifest_hash(manifest)
    primary.write_text("{corrupt", encoding="utf-8")
    backup = primary.with_name(primary.name + ".bak")
    backup.write_text(json.dumps(manifest), encoding="utf-8")

    loaded, source = load_library_manifest(primary)
    assert source == backup
    assert loaded["content_hash"] == manifest["content_hash"]
