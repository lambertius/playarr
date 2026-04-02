"""
Tests for the AI enrichment pipeline — prompt builder and response parser.

Includes the Amanda Palmer "bad scrape" fixture: metadata from a Hilary Duff
video was accidentally pasted onto an Amanda Palmer music video.  The AI
pipeline must detect this mismatch and propose correct metadata.
"""
import json
import pytest

from app.ai.prompt_builder import build_metadata_enrichment_prompt, SYSTEM_PROMPT
from app.ai.response_parser import (
    parse_enrichment_response,
    extract_ai_metadata,
    strip_markdown_fences,
    _validate_schema,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

AMANDA_PALMER_SCRAPED = {
    "artist": "Hilary Duff",
    "title": "With Love",
    "album": "Dignity",
    "year": 2007,
    "genres": ["Pop"],
    "plot": (
        "Hilary Duff performs 'With Love' in a glamorous downtown setting "
        "with neon lights and choreographed backup dancers."
    ),
}

AMANDA_PALMER_FILENAME = "Amanda Palmer - Runs in the Family.mkv"
AMANDA_PALMER_URL = "https://www.youtube.com/watch?v=EXAMPLE123"
AMANDA_PALMER_DURATION = 257.0  # ~4:17

AMANDA_PALMER_MISMATCH_SIGNALS = [
    {
        "name": "artist_filename_mismatch",
        "score": 0.95,
        "details": "Filename says 'Amanda Palmer' but metadata says 'Hilary Duff'",
        "weight": 1.0,
    },
    {
        "name": "title_filename_mismatch",
        "score": 0.90,
        "details": "Filename says 'Runs in the Family' but metadata says 'With Love'",
        "weight": 0.8,
    },
]

# Simulated correct AI response for the Amanda Palmer fixture
AMANDA_PALMER_AI_RESPONSE = json.dumps({
    "identity": {
        "candidate_artist": "Amanda Palmer",
        "candidate_title": "Runs in the Family",
        "evidence": {
            "filename_match": True,
            "url_match": False,
            "metadata_consistent": False,
            "known_song": True,
        },
    },
    "mismatch": {
        "is_mismatch": True,
        "severity": "high",
        "reasons": [
            "Scraped artist 'Hilary Duff' does not match filename artist 'Amanda Palmer'",
            "Scraped title 'With Love' does not match filename title 'Runs in the Family'",
            "Plot describes a Hilary Duff music video, not Amanda Palmer content",
        ],
    },
    "proposed": {
        "artist": "Amanda Palmer",
        "title": "Runs in the Family",
        "album": "Who Killed Amanda Palmer",
        "year": 2008,
        "genres": ["Alternative", "Cabaret", "Indie Rock"],
        "plot": (
            "Amanda Palmer performs 'Runs in the Family' from her 2008 solo debut "
            "'Who Killed Amanda Palmer'. The darkly theatrical music video matches "
            "Palmer's signature cabaret punk aesthetic."
        ),
        "director": None,
        "studio": "Roadrunner Records",
        "tags": ["cabaret punk", "theatrical", "solo artist", "dark cabaret"],
    },
    "confidence": {
        "artist": 0.98,
        "title": 0.97,
        "album": 0.90,
        "year": 0.95,
        "genres": 0.85,
        "plot": 0.65,
        "director": 0.1,
        "studio": 0.75,
        "tags": 0.70,
    },
    "change_summary": (
        "MISMATCH DETECTED: Metadata was from Hilary Duff - 'With Love' but file is "
        "Amanda Palmer - 'Runs in the Family'. Corrected artist, title, album, year, "
        "genres, and plot."
    ),
})


# ── Prompt Builder Tests ──────────────────────────────────────────────────

class TestBuildPrompt:
    """Tests for build_metadata_enrichment_prompt()."""

    def test_basic_prompt_contains_metadata(self):
        """Prompt should include all scraped metadata fields."""
        prompt = build_metadata_enrichment_prompt(
            {"artist": "Foo Fighters", "title": "My Hero", "album": "The Colour and the Shape",
             "year": 1997, "genres": ["Rock", "Alternative"], "plot": "A hero walks among us."},
        )
        assert "Foo Fighters" in prompt
        assert "My Hero" in prompt
        assert "The Colour and the Shape" in prompt
        assert "1997" in prompt
        assert "Rock" in prompt

    def test_prompt_includes_filename(self):
        prompt = build_metadata_enrichment_prompt(
            {"artist": "Test", "title": "Song"},
            video_filename="Test - Song [1080p].mkv",
        )
        assert "Test - Song [1080p].mkv" in prompt

    def test_prompt_includes_duration(self):
        prompt = build_metadata_enrichment_prompt(
            {"artist": "Test", "title": "Song"},
            duration_seconds=185.0,
        )
        assert "3:05" in prompt
        assert "185s" in prompt

    def test_prompt_includes_mismatch_signals(self):
        """Mismatch signals from the heuristic detector should be in the prompt."""
        prompt = build_metadata_enrichment_prompt(
            AMANDA_PALMER_SCRAPED,
            video_filename=AMANDA_PALMER_FILENAME,
            mismatch_signals=AMANDA_PALMER_MISMATCH_SIGNALS,
        )
        assert "artist_filename_mismatch" in prompt
        assert "Amanda Palmer" in prompt
        assert "Pay special attention" in prompt

    def test_prompt_includes_fingerprint_hint(self):
        prompt = build_metadata_enrichment_prompt(
            {"artist": "Test", "title": "Song"},
            fingerprint_hint={"artist": "Real Artist", "title": "Real Song", "confidence": 0.92},
        )
        assert "Real Artist" in prompt
        assert "Real Song" in prompt
        assert "92%" in prompt

    def test_prompt_handles_missing_fields(self):
        """Should handle None/missing fields gracefully with 'Unknown'."""
        prompt = build_metadata_enrichment_prompt(
            {"artist": None, "title": None},
        )
        assert "Unknown" in prompt

    def test_prompt_json_structure_hint(self):
        """The prompt should ask for JSON with identity, proposed, confidence keys."""
        prompt = build_metadata_enrichment_prompt(
            {"artist": "Test", "title": "Song"},
        )
        assert '"identity"' in prompt
        assert '"proposed"' in prompt
        assert '"confidence"' in prompt
        assert '"mismatch"' in prompt

    def test_system_prompt_exists(self):
        assert "metadata expert" in SYSTEM_PROMPT.lower()
        assert "json" in SYSTEM_PROMPT.lower()

    def test_amanda_palmer_full_prompt(self):
        """Full Amanda Palmer bad-scrape prompt includes all context signals."""
        prompt = build_metadata_enrichment_prompt(
            AMANDA_PALMER_SCRAPED,
            video_filename=AMANDA_PALMER_FILENAME,
            source_url=AMANDA_PALMER_URL,
            duration_seconds=AMANDA_PALMER_DURATION,
            mismatch_signals=AMANDA_PALMER_MISMATCH_SIGNALS,
        )
        # Should contain scraped (wrong) data
        assert "Hilary Duff" in prompt
        assert "With Love" in prompt
        # Should contain filename (correct) context
        assert "Amanda Palmer" in prompt
        assert "Runs in the Family" in prompt
        # Should contain mismatch warnings
        assert "artist_filename_mismatch" in prompt
        # Should contain duration
        assert "4:17" in prompt


# ── Response Parser Tests ─────────────────────────────────────────────────

class TestParseResponse:
    """Tests for parse_enrichment_response()."""

    def test_parse_valid_json(self):
        parsed, was_retry = parse_enrichment_response(AMANDA_PALMER_AI_RESPONSE)
        assert not was_retry
        assert parsed["identity"]["candidate_artist"] == "Amanda Palmer"
        assert parsed["mismatch"]["is_mismatch"] is True
        assert parsed["proposed"]["artist"] == "Amanda Palmer"

    def test_parse_json_with_markdown_fences(self):
        """Should strip ```json fences."""
        wrapped = f"```json\n{AMANDA_PALMER_AI_RESPONSE}\n```"
        parsed, _ = parse_enrichment_response(wrapped)
        assert parsed["proposed"]["artist"] == "Amanda Palmer"

    def test_parse_json_with_surrounding_text(self):
        """Should extract JSON from surrounding commentary."""
        wrapped = f"Here is the corrected metadata:\n\n{AMANDA_PALMER_AI_RESPONSE}\n\nHope this helps!"
        parsed, _ = parse_enrichment_response(wrapped)
        assert parsed["proposed"]["artist"] == "Amanda Palmer"

    def test_parse_invalid_json_raises(self):
        """Should raise ValueError for completely invalid responses."""
        with pytest.raises(ValueError, match="not valid JSON"):
            parse_enrichment_response("This is not JSON at all.")

    def test_parse_missing_required_keys_raises(self):
        """JSON without required keys should fail validation."""
        bad = json.dumps({"artist": "Test", "title": "Song"})
        with pytest.raises(ValueError):
            parse_enrichment_response(bad)

    def test_parse_with_retry(self):
        """When first parse fails but retry succeeds."""
        call_count = 0

        def mock_retry(repair_prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            return AMANDA_PALMER_AI_RESPONSE

        parsed, was_retry = parse_enrichment_response(
            "broken json {{{",
            retry_fn=mock_retry,
        )
        assert was_retry
        assert call_count == 1
        assert parsed["proposed"]["artist"] == "Amanda Palmer"


class TestExtractMetadata:
    """Tests for extract_ai_metadata()."""

    def test_extract_amanda_palmer(self):
        parsed = json.loads(AMANDA_PALMER_AI_RESPONSE)
        meta = extract_ai_metadata(parsed, tokens=500, model_name="gpt-4o-mini")

        assert meta["artist"] == "Amanda Palmer"
        assert meta["title"] == "Runs in the Family"
        assert meta["album"] == "Who Killed Amanda Palmer"
        assert meta["year"] == 2008
        assert meta["genres"] == ["Alternative", "Cabaret", "Indie Rock"]
        assert meta["studio"] == "Roadrunner Records"
        assert meta["tags"] == ["cabaret punk", "theatrical", "solo artist", "dark cabaret"]

        # Confidence
        assert meta["field_scores"]["artist"] == pytest.approx(0.98)
        assert meta["overall_confidence"] > 0.8

        # Identity
        assert meta["identity"]["candidate_artist"] == "Amanda Palmer"
        assert meta["identity"]["evidence"]["known_song"] is True
        assert meta["identity"]["evidence"]["metadata_consistent"] is False

        # Mismatch
        assert meta["mismatch_info"]["is_mismatch"] is True
        assert meta["mismatch_info"]["severity"] == "high"
        assert len(meta["mismatch_info"]["reasons"]) == 3

        # Summary
        assert "MISMATCH" in meta["change_summary"]

    def test_extract_year_string_conversion(self):
        """Year as string should be converted to int."""
        parsed = json.loads(AMANDA_PALMER_AI_RESPONSE)
        parsed["proposed"]["year"] = "2008"
        meta = extract_ai_metadata(parsed)
        assert meta["year"] == 2008

    def test_extract_year_invalid_string(self):
        """Invalid year string should become None."""
        parsed = json.loads(AMANDA_PALMER_AI_RESPONSE)
        parsed["proposed"]["year"] = "unknown"
        meta = extract_ai_metadata(parsed)
        assert meta["year"] is None

    def test_extract_genres_from_string(self):
        """If AI returns genres as comma-separated string, parse it."""
        parsed = json.loads(AMANDA_PALMER_AI_RESPONSE)
        parsed["proposed"]["genres"] = "Rock, Alternative, Punk"
        meta = extract_ai_metadata(parsed)
        assert meta["genres"] == ["Rock", "Alternative", "Punk"]

    def test_confidence_clamping(self):
        """Confidence values should be clamped to 0.0–1.0."""
        parsed = json.loads(AMANDA_PALMER_AI_RESPONSE)
        parsed["confidence"]["artist"] = 1.5
        parsed["confidence"]["title"] = -0.3
        meta = extract_ai_metadata(parsed)
        assert meta["field_scores"]["artist"] == 1.0
        assert meta["field_scores"]["title"] == 0.0


class TestStripMarkdownFences:
    def test_no_fences(self):
        assert strip_markdown_fences('{"a": 1}') == '{"a": 1}'

    def test_json_fence(self):
        assert strip_markdown_fences('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_plain_fence(self):
        assert strip_markdown_fences('```\n{"a": 1}\n```') == '{"a": 1}'


class TestValidateSchema:
    def test_valid(self):
        parsed = json.loads(AMANDA_PALMER_AI_RESPONSE)
        _validate_schema(parsed)  # should not raise

    def test_missing_identity(self):
        with pytest.raises(ValueError, match="Missing required keys"):
            _validate_schema({"proposed": {"artist": "X"}, "confidence": {}})

    def test_missing_proposed(self):
        with pytest.raises(ValueError, match="Missing required keys"):
            _validate_schema({"identity": {}, "confidence": {}})

    def test_empty_proposed(self):
        with pytest.raises(ValueError, match="at least"):
            _validate_schema({
                "identity": {},
                "proposed": {},
                "confidence": {},
            })

    def test_not_a_dict(self):
        with pytest.raises(ValueError, match="Expected JSON object"):
            _validate_schema([1, 2, 3])
