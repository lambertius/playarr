"""
Playarr Matching & Confidence Scoring System
=============================================

Resolves parsed "Artist / Title / Album" inputs to canonical entities
(MusicBrainz IDs where possible), assigns a confidence score, records
provenance, and supports safe refresh + user "Fix Match."

Modules:
    normalization  — Artist/title/album cleaning and key generation
    candidates     — Candidate dataclasses and provider adapter
    scoring        — Feature computation and weighted scoring engine
    resolver       — Orchestration: normalize → candidates → score → persist
    hysteresis     — Anti-flapping and user-pinning logic
    models         — SQLAlchemy persistence (MatchResult, MatchCandidate, …)
    schemas        — Pydantic request/response models for API
"""
