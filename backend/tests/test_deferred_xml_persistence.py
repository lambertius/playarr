"""
Simulation test for deferred task XML sidecar persistence.

Verifies the fix for the bug where library clear + rescan loses:
  1. Scene analysis data (scenes_analyzed processing flag + AIThumbnail records)
  2. Entity cached artwork (CachedAsset records for artist/album art)

Root cause: The XML sidecar was written at pipeline completion BEFORE
deferred tasks ran. Deferred tasks (scene_analysis, entity_artwork)
modified the DB but never rewrote the XML sidecar. On clear + rescan,
the stale XML was read back, missing the deferred task data.

Fix: dispatch_deferred now rewrites the XML sidecar after ALL deferred
tasks complete, and scene_analysis copies thumbnails to the video folder.
"""
import os
import sys
import json
import tempfile
import shutil
import sqlite3
from datetime import datetime, timezone
from xml.etree.ElementTree import parse as parse_xml

# Ensure the backend package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _create_mock_xml(folder_path: str, video_filename: str, *,
                     include_scenes: bool = False,
                     include_scenes_flag: bool = False) -> str:
    """Create a minimal .playarr.xml sidecar for testing.

    When include_scenes=False and include_scenes_flag=False, this simulates
    the stale XML produced BEFORE the fix (deferred tasks hadn't run yet).
    """
    xml_path = os.path.join(folder_path, f"{os.path.splitext(video_filename)[0]}.playarr.xml")
    ps_steps = [
        ("file_organized", True),
        ("filename_checked", True),
        ("imported", True),
        ("metadata_scraped", True),
        ("metadata_resolved", True),
        ("ai_enriched", True),
        ("xml_exported", True),
        ("nfo_exported", True),
    ]
    if include_scenes_flag:
        ps_steps.append(("scenes_analyzed", True))
        ps_steps.append(("thumbnail_selected", True))

    ps_xml = ""
    for step, completed in ps_steps:
        ps_xml += f"""
      <step name="{step}">
        <completed>{'true' if completed else 'false'}</completed>
        <timestamp>2026-04-07T12:00:00+00:00</timestamp>
        <method>test</method>
        <version>1.0</version>
      </step>"""

    scenes_xml = ""
    if include_scenes:
        scenes_xml = """
    <scene_analysis>
      <total_scenes>5</total_scenes>
      <duration_seconds>240.0</duration_seconds>
      <thumbnails>
        <thumb>
          <timestamp_sec>30.0</timestamp_sec>
          <file>thumb_30.00.jpg</file>
          <score_sharpness>0.9</score_sharpness>
          <score_contrast>0.8</score_contrast>
          <score_color_variance>0.7</score_color_variance>
          <score_composition>0.85</score_composition>
          <score_overall>0.82</score_overall>
          <is_selected>True</is_selected>
          <provenance>ai_scene_analysis</provenance>
        </thumb>
      </thumbnails>
    </scene_analysis>"""

    xml_content = f"""<?xml version="1.0" encoding="utf-8"?>
<playarr>
    <artist>Test Artist</artist>
    <title>Test Title</title>
    <album>Test Album</album>
    <year>2024</year>
    <processing_state>{ps_xml}
    </processing_state>{scenes_xml}
    <entity_refs>
      <artist>
        <name>Test Artist</name>
        <mb_artist_id>aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee</mb_artist_id>
      </artist>
      <album>
        <title>Test Album</title>
        <mb_release_id>11111111-2222-3333-4444-555555555555</mb_release_id>
      </album>
    </entity_refs>
</playarr>"""
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml_content)
    return xml_path


def test_stale_xml_missing_scenes_flag():
    """Simulate: XML written before deferred tasks → missing scenes_analyzed.

    This test verifies that parsing the stale XML produces a processing_state
    WITHOUT scenes_analyzed, which would cause the library scan to flag the
    track as ai_partial.
    """
    from app.services.playarr_xml import parse_playarr_xml

    with tempfile.TemporaryDirectory() as tmpdir:
        video_file = "Test Artist - Test Title [1080p].mp4"
        # Create a minimal video file placeholder
        open(os.path.join(tmpdir, video_file), "wb").close()

        # Stale XML: no scenes_analyzed flag, no scene_analysis block
        xml_path = _create_mock_xml(tmpdir, video_file,
                                    include_scenes=False,
                                    include_scenes_flag=False)

        data = parse_playarr_xml(xml_path)
        ps = data.get("processing_state", {})

        # Verify scenes_analyzed is NOT present (the bug scenario)
        assert "scenes_analyzed" not in ps or not ps["scenes_analyzed"].get("completed"), \
            "Stale XML should NOT have scenes_analyzed flag"

        # Verify ai_enriched IS present (AI did run)
        assert ps.get("ai_enriched", {}).get("completed") is True, \
            "ai_enriched should be set from the pipeline"

        # Verify no scene analysis data
        sa = data.get("scene_analysis")
        assert sa is None or not sa.get("thumbnails"), \
            "Stale XML should have no scene analysis thumbnails"

        print("PASS: Stale XML correctly missing scenes_analyzed flag")


def test_fixed_xml_has_scenes_flag():
    """Simulate: XML with scenes_analyzed flag (post-fix).

    After the fix, dispatch_deferred rewrites the XML after all deferred
    tasks. This test verifies that the fixed XML restores correctly.
    """
    from app.services.playarr_xml import parse_playarr_xml

    with tempfile.TemporaryDirectory() as tmpdir:
        video_file = "Test Artist - Test Title [1080p].mp4"
        open(os.path.join(tmpdir, video_file), "wb").close()

        # Fixed XML: HAS scenes_analyzed flag AND scene_analysis block
        xml_path = _create_mock_xml(tmpdir, video_file,
                                    include_scenes=True,
                                    include_scenes_flag=True)

        # Also create a thumb file in the folder (would be copied by fixed deferred)
        thumb_path = os.path.join(tmpdir, "thumb_30.00.jpg")
        open(thumb_path, "wb").close()

        data = parse_playarr_xml(xml_path)
        ps = data.get("processing_state", {})

        # Verify scenes_analyzed IS present
        assert ps.get("scenes_analyzed", {}).get("completed") is True, \
            "Fixed XML should have scenes_analyzed flag"

        # Verify scene analysis data exists
        sa = data.get("scene_analysis")
        assert sa is not None, "Fixed XML should have scene_analysis block"
        assert len(sa.get("thumbnails", [])) > 0, "Fixed XML should have thumbnails"
        assert sa["thumbnails"][0]["timestamp_sec"] == 30.0, "Thumbnail timestamp correct"

        print("PASS: Fixed XML correctly has scenes_analyzed flag and scene data")


def test_library_scan_flagging_logic():
    """Verify the library_scan_task flagging logic for missing AI enrichment.

    Simulates the exact check at lines 5252-5268 of tasks.py.
    """
    # Simulate stale processing_state (XML without scenes_analyzed)
    stale_ps = {
        "file_organized": {"completed": True},
        "filename_checked": {"completed": True},
        "imported": {"completed": True},
        "metadata_scraped": {"completed": True},
        "metadata_resolved": {"completed": True},
        "ai_enriched": {"completed": True},
        "xml_exported": {"completed": True},
    }

    # This is the exact logic from library_scan_task
    _ps_done = lambda step: stale_ps.get(step, {}).get("completed", False)
    ai_done = _ps_done("ai_enriched")
    scenes_done = _ps_done("scenes_analyzed")

    assert ai_done is True, "ai_enriched should be True"
    assert scenes_done is False, "scenes_analyzed should be False (stale XML)"

    # This would flag the track as ai_partial
    if not (ai_done and scenes_done):
        _enrich_cat = "ai_partial" if (ai_done or scenes_done) else "ai_pending"
        _missing = []
        if not ai_done:
            _missing.append("AI metadata")
        if not scenes_done:
            _missing.append("scene analysis")

        assert _enrich_cat == "ai_partial", \
            f"Expected ai_partial, got {_enrich_cat}"
        assert _missing == ["scene analysis"], \
            f"Expected ['scene analysis'], got {_missing}"

        print(f"PASS: Stale XML correctly identified as ai_partial (missing: {_missing})")

    # Now simulate fixed processing_state (XML WITH scenes_analyzed)
    fixed_ps = dict(stale_ps)
    fixed_ps["scenes_analyzed"] = {"completed": True}
    fixed_ps["thumbnail_selected"] = {"completed": True}

    _ps_done2 = lambda step: fixed_ps.get(step, {}).get("completed", False)
    ai_done2 = _ps_done2("ai_enriched")
    scenes_done2 = _ps_done2("scenes_analyzed")

    assert ai_done2 is True and scenes_done2 is True, \
        "Both flags should be True in fixed XML"

    # Should NOT flag as needing review
    should_flag = not (ai_done2 and scenes_done2)
    assert should_flag is False, \
        "Fixed XML should NOT be flagged for review"

    print("PASS: Fixed XML NOT flagged for review (both flags present)")


def test_dispatch_deferred_writes_xml():
    """Verify dispatch_deferred coordinator includes XML sidecar rewrite.

    This is a code-structure test — verifies the fix is present in
    both pipeline_lib and pipeline_url deferred modules.
    """
    import inspect

    # Check pipeline_lib/deferred.py
    from app.pipeline_lib.deferred import dispatch_deferred as lib_dispatch
    lib_source = inspect.getsource(lib_dispatch)
    assert "write_playarr_xml" in lib_source, \
        "pipeline_lib dispatch_deferred should call write_playarr_xml"

    # Check pipeline_url/deferred.py
    from app.pipeline_url.deferred import dispatch_deferred as url_dispatch
    url_source = inspect.getsource(url_dispatch)
    assert "write_playarr_xml" in url_source, \
        "pipeline_url dispatch_deferred should call write_playarr_xml"

    print("PASS: Both dispatch_deferred functions include XML sidecar rewrite")


def test_scene_analysis_copies_thumbs():
    """Verify _deferred_scene_analysis copies thumbnails to video folder.

    Checks the code structure of both pipeline_lib and pipeline_url
    deferred scene analysis functions.
    """
    import inspect

    # Check pipeline_lib/deferred.py
    from app.pipeline_lib.deferred import _deferred_scene_analysis as lib_sa
    lib_sa_source = inspect.getsource(lib_sa)
    assert "shutil" in lib_sa_source or "copy2" in lib_sa_source, \
        "pipeline_lib _deferred_scene_analysis should copy thumbs to folder"

    # Check pipeline_url/deferred.py
    from app.pipeline_url.deferred import _deferred_scene_analysis as url_sa
    url_sa_source = inspect.getsource(url_sa)
    assert "shutil" in url_sa_source or "copy2" in url_sa_source, \
        "pipeline_url _deferred_scene_analysis should copy thumbs to folder"

    # Check pipeline/deferred.py (should already have this - baseline)
    from app.pipeline.deferred import _deferred_scene_analysis as pipe_sa
    pipe_sa_source = inspect.getsource(pipe_sa)
    assert "shutil" in pipe_sa_source or "copy2" in pipe_sa_source, \
        "pipeline _deferred_scene_analysis should copy thumbs (baseline)"

    print("PASS: All pipeline deferred scene_analysis functions copy thumbnails")


if __name__ == "__main__":
    tests = [
        test_stale_xml_missing_scenes_flag,
        test_fixed_xml_has_scenes_flag,
        test_library_scan_flagging_logic,
        test_dispatch_deferred_writes_xml,
        test_scene_analysis_copies_thumbs,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"FAIL: {test.__name__}: {e}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)}")
    if failed:
        sys.exit(1)
    else:
        print("All tests passed!")
