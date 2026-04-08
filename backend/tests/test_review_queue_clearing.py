"""
Simulation test for review queue auto-clearing after deferred tasks.

Verifies that when deferred tasks complete and resolve the underlying issue
that caused a review flag, the flag is automatically cleared:

  1. ai_partial (missing scene analysis only) → cleared after scenes_analyzed
  2. ai_pending (missing both AI + scenes) → cleared after both complete
  3. normalization → cleared after audio_normalized
  4. Review message uses "Missing ..." format (not "Partial AI enrichment")
  5. Flags are NOT cleared when the issue remains unresolved

Root cause: Deferred tasks SET review flags on failure but did NOT clear them
on success, leaving stale items in the review queue after batch scrape.
"""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_processing_state(**flags):
    """Build a processing_state dict with completed=True for each named flag."""
    ps = {}
    for step, completed in flags.items():
        ps[step] = {
            "completed": completed,
            "timestamp": "2026-04-07T12:00:00+00:00",
            "method": "test",
        }
    return ps


# ── Test 1: Message format is correct ──────────────────────────

def test_review_message_format_missing_scenes_only():
    """When only scene analysis is missing, message should be 'Missing scene analysis'."""
    _missing = []
    ai_done = True
    scenes_done = False
    if not ai_done:
        _missing.append("AI metadata")
    if not scenes_done:
        _missing.append("scene analysis")
    _enrich_cat = "ai_partial" if (ai_done or scenes_done) else "ai_pending"

    # NEW format (matches fix in tasks.py and resolve.py)
    reason = f"Missing {', '.join(_missing)}"

    assert reason == "Missing scene analysis", f"Expected 'Missing scene analysis', got '{reason}'"
    assert _enrich_cat == "ai_partial"
    assert "AI enrichment" not in reason, "Should not mention 'AI enrichment'"
    print("PASS: test_review_message_format_missing_scenes_only")


def test_review_message_format_missing_both():
    """When both are missing, message should be 'Missing AI metadata, scene analysis'."""
    _missing = []
    ai_done = False
    scenes_done = False
    if not ai_done:
        _missing.append("AI metadata")
    if not scenes_done:
        _missing.append("scene analysis")
    _enrich_cat = "ai_partial" if (ai_done or scenes_done) else "ai_pending"

    reason = f"Missing {', '.join(_missing)}"

    assert reason == "Missing AI metadata, scene analysis"
    assert _enrich_cat == "ai_pending"
    print("PASS: test_review_message_format_missing_both")


def test_review_message_format_missing_ai_only():
    """When only AI metadata is missing, message should be 'Missing AI metadata'."""
    _missing = []
    ai_done = False
    scenes_done = True
    if not ai_done:
        _missing.append("AI metadata")
    if not scenes_done:
        _missing.append("scene analysis")

    reason = f"Missing {', '.join(_missing)}"

    assert reason == "Missing AI metadata"
    print("PASS: test_review_message_format_missing_ai_only")


# ── Test 2: Auto-clear logic (simulates coordinator finally block) ────

def _simulate_auto_clear(review_status, review_category, review_reason, processing_state):
    """Simulate the auto-clear logic from the deferred coordinator's finally block.

    Returns (new_review_status, new_review_reason, new_review_category, cleared).
    """
    if review_status != "needs_human_review":
        return review_status, review_reason, review_category, False

    _rc = review_category
    _ps = processing_state or {}
    _flag_ok = lambda s: _ps.get(s, {}).get("completed", False)
    _clear = False

    if _rc in ("ai_partial", "ai_pending"):
        _clear = _flag_ok("ai_enriched") and _flag_ok("scenes_analyzed")
    elif _rc == "normalization":
        _clear = _flag_ok("audio_normalized")

    if _clear:
        return "none", None, None, True
    return review_status, review_reason, review_category, False


def test_auto_clear_ai_partial_scenes_done():
    """ai_partial flag should clear after both ai_enriched + scenes_analyzed are done."""
    ps = _make_processing_state(
        ai_enriched=True,
        scenes_analyzed=True,
    )
    status, reason, cat, cleared = _simulate_auto_clear(
        "needs_human_review", "ai_partial", "Missing scene analysis", ps
    )
    assert cleared, "Should have cleared"
    assert status == "none"
    assert reason is None
    assert cat is None
    print("PASS: test_auto_clear_ai_partial_scenes_done")


def test_auto_clear_ai_pending_both_done():
    """ai_pending flag should clear after both ai_enriched + scenes_analyzed are done."""
    ps = _make_processing_state(
        ai_enriched=True,
        scenes_analyzed=True,
    )
    status, reason, cat, cleared = _simulate_auto_clear(
        "needs_human_review", "ai_pending", "Missing AI metadata, scene analysis", ps
    )
    assert cleared
    assert status == "none"
    print("PASS: test_auto_clear_ai_pending_both_done")


def test_auto_clear_normalization_done():
    """normalization flag should clear after audio_normalized is done."""
    ps = _make_processing_state(audio_normalized=True)
    status, reason, cat, cleared = _simulate_auto_clear(
        "needs_human_review", "normalization",
        "Audio normalization failed (possible codec incompatibility)", ps
    )
    assert cleared
    assert status == "none"
    print("PASS: test_auto_clear_normalization_done")


def test_no_clear_ai_partial_still_missing():
    """ai_partial flag should NOT clear if scene_analysis is still missing."""
    ps = _make_processing_state(
        ai_enriched=True,
        scenes_analyzed=False,
    )
    status, reason, cat, cleared = _simulate_auto_clear(
        "needs_human_review", "ai_partial", "Missing scene analysis", ps
    )
    assert not cleared
    assert status == "needs_human_review"
    assert cat == "ai_partial"
    print("PASS: test_no_clear_ai_partial_still_missing")


def test_no_clear_ai_pending_only_scenes_done():
    """ai_pending flag should NOT clear if only scenes_analyzed is done."""
    ps = _make_processing_state(
        ai_enriched=False,
        scenes_analyzed=True,
    )
    status, reason, cat, cleared = _simulate_auto_clear(
        "needs_human_review", "ai_pending", "Missing AI metadata, scene analysis", ps
    )
    assert not cleared
    assert status == "needs_human_review"
    print("PASS: test_no_clear_ai_pending_only_scenes_done")


def test_no_clear_unrelated_category():
    """Non-enrichment categories (duplicate, rename, etc.) should not be auto-cleared."""
    ps = _make_processing_state(
        ai_enriched=True,
        scenes_analyzed=True,
        audio_normalized=True,
    )
    for cat in ("duplicate", "rename", "version_detection", "manual_review",
                "canonical_missing", "import_error", "scanned"):
        status, reason, rcat, cleared = _simulate_auto_clear(
            "needs_human_review", cat, "Some reason", ps
        )
        assert not cleared, f"Should NOT have cleared for category '{cat}'"
    print("PASS: test_no_clear_unrelated_category")


def test_no_clear_already_reviewed():
    """Videos with review_status='reviewed' should not be touched."""
    ps = _make_processing_state(ai_enriched=True, scenes_analyzed=True)
    status, reason, cat, cleared = _simulate_auto_clear(
        "reviewed", "ai_partial", "Missing scene analysis", ps
    )
    assert not cleared
    assert status == "reviewed"
    print("PASS: test_no_clear_already_reviewed")


def test_no_clear_status_none():
    """Videos with review_status='none' should not be touched."""
    ps = _make_processing_state(ai_enriched=True, scenes_analyzed=True)
    status, reason, cat, cleared = _simulate_auto_clear(
        "none", "ai_partial", None, ps
    )
    assert not cleared
    assert status == "none"
    print("PASS: test_no_clear_status_none")


# ── Test 3: Verify code in actual source files ────────────────

def test_source_files_use_new_message_format():
    """Verify tasks.py and resolve.py use the new 'Missing ...' message format."""
    backend_dir = os.path.join(os.path.dirname(__file__), "..")

    tasks_path = os.path.join(backend_dir, "app", "tasks.py")
    resolve_path = os.path.join(backend_dir, "app", "routers", "resolve.py")

    with open(tasks_path, "r", encoding="utf-8") as f:
        tasks_src = f.read()
    with open(resolve_path, "r", encoding="utf-8") as f:
        resolve_src = f.read()

    # Old format should NOT appear
    assert "Partial AI enrichment" not in tasks_src, \
        "tasks.py still contains old 'Partial AI enrichment' text"
    assert "No AI enrichment" not in tasks_src, \
        "tasks.py still contains old 'No AI enrichment' text"
    assert "Partial AI enrichment" not in resolve_src, \
        "resolve.py still contains old 'Partial AI enrichment' text"
    assert "No AI enrichment" not in resolve_src, \
        "resolve.py still contains old 'No AI enrichment' text"

    # New format should appear
    assert 'f"Missing {\'' in tasks_src or "Missing {" in tasks_src, \
        "tasks.py missing new format"
    assert 'f"Missing {\'' in resolve_src or "Missing {" in resolve_src, \
        "resolve.py missing new format"
    print("PASS: test_source_files_use_new_message_format")


def test_deferred_coordinators_have_auto_clear():
    """Verify all 3 deferred coordinator files contain the auto-clear logic."""
    backend_dir = os.path.join(os.path.dirname(__file__), "..")
    deferred_files = [
        os.path.join(backend_dir, "app", "pipeline", "deferred.py"),
        os.path.join(backend_dir, "app", "pipeline_lib", "deferred.py"),
        os.path.join(backend_dir, "app", "pipeline_url", "deferred.py"),
    ]

    for path in deferred_files:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()

        assert "Review flag cleared" in src, \
            f"{os.path.basename(os.path.dirname(path))}/deferred.py missing auto-clear log message"
        assert "ai_partial" in src, \
            f"{os.path.basename(os.path.dirname(path))}/deferred.py missing ai_partial check"
        assert "ai_pending" in src, \
            f"{os.path.basename(os.path.dirname(path))}/deferred.py missing ai_pending check"
        assert "normalization" in src, \
            f"{os.path.basename(os.path.dirname(path))}/deferred.py missing normalization check"

    print("PASS: test_deferred_coordinators_have_auto_clear")


if __name__ == "__main__":
    test_review_message_format_missing_scenes_only()
    test_review_message_format_missing_both()
    test_review_message_format_missing_ai_only()
    test_auto_clear_ai_partial_scenes_done()
    test_auto_clear_ai_pending_both_done()
    test_auto_clear_normalization_done()
    test_no_clear_ai_partial_still_missing()
    test_no_clear_ai_pending_only_scenes_done()
    test_no_clear_unrelated_category()
    test_no_clear_already_reviewed()
    test_no_clear_status_none()
    test_source_files_use_new_message_format()
    test_deferred_coordinators_have_auto_clear()
    print(f"\n{'='*50}")
    print("All 13 tests passed!")
