"""Release-gate tests for SIDE-002 and SIDE-003."""
from pathlib import Path

import pytest

from app.services.playarr_xml import parse_playarr_xml
from app.services.sidecar_store import (
    SidecarValidationError,
    atomic_write_sidecar,
    validate_playarr_sidecar,
)


def _payload(*, artist: str = "Artist", title: str = "Title", marker: str = "one") -> bytes:
    return f"""<?xml version='1.0' encoding='utf-8'?>
<playarr version="2" schemaVersion="2" playarrVersion="test"
 sidecarRevision="1" entityRevision="1" generatedAt="2026-08-01T00:00:00Z"
 contentHash="sha256:test" entityId="entity-test" playarrVideoId="PVD-test">
  <portable_identity entityId="entity-test" videoId="PVD-test" trackId="PTR-test" />
  <identity><artist>{artist}</artist><title>{title}</title></identity>
  <marker>{marker}</marker>
</playarr>""".encode()


def test_atomic_write_creates_valid_v2_document(tmp_path: Path):
    destination = tmp_path / "track.playarr.xml"

    result = atomic_write_sidecar(destination, _payload())

    assert result == destination
    assert destination.read_bytes() == _payload()
    validate_playarr_sidecar(destination)
    parsed = parse_playarr_xml(str(destination))
    assert parsed is not None
    assert parsed["xml_version"] == "2"
    assert parsed["playarr_video_id"] == "PVD-test"
    assert parsed["entity_stable_id"] == "entity-test"
    assert parsed["playarr_track_id"] == "PTR-test"
    assert parsed["entity_revision"] == 1


def test_atomic_write_retains_last_valid_backup(tmp_path: Path):
    destination = tmp_path / "track.playarr.xml"
    first = _payload(marker="first")
    second = _payload(marker="second")
    atomic_write_sidecar(destination, first)

    atomic_write_sidecar(destination, second)

    assert destination.read_bytes() == second
    assert destination.with_name("track.playarr.xml.bak").read_bytes() == first


def test_invalid_replacement_does_not_damage_authoritative_file(tmp_path: Path):
    destination = tmp_path / "track.playarr.xml"
    valid = _payload()
    atomic_write_sidecar(destination, valid)

    with pytest.raises(SidecarValidationError):
        atomic_write_sidecar(destination, b"<playarr><identity>")

    assert destination.read_bytes() == valid
    assert not list(tmp_path.glob("*.tmp"))


def test_unknown_schema_is_rejected(tmp_path: Path):
    destination = tmp_path / "future.playarr.xml"
    destination.write_bytes(_payload().replace(b'schemaVersion="2"', b'schemaVersion="99"'))

    with pytest.raises(SidecarValidationError, match="unsupported"):
        validate_playarr_sidecar(destination)


def test_v2_requires_stable_video_identity(tmp_path: Path):
    destination = tmp_path / "unstable.playarr.xml"
    destination.write_bytes(
        _payload().replace(b' playarrVideoId="PVD-test"', b"")
        .replace(b' videoId="PVD-test"', b"")
    )

    with pytest.raises(SidecarValidationError, match="playarrVideoId"):
        validate_playarr_sidecar(destination)


def test_v2_requires_stable_entity_identity(tmp_path: Path):
    destination = tmp_path / "unstable-entity.playarr.xml"
    destination.write_bytes(
        _payload().replace(b' entityId="entity-test"', b"")
    )

    with pytest.raises(SidecarValidationError, match="entityId"):
        validate_playarr_sidecar(destination)
