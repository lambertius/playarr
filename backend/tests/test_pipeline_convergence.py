"""PIPE-001/PIPE-003 convergence, checkpoint and event acceptance."""
from pathlib import Path

import app.pipeline_url.workspace as workspace_module
from app.pipeline_lib.mutation_plan import build_plan_from_workspace
from app.pipeline_url.workspace import ImportWorkspace


class ArtifactWorkspace:
    def __init__(self, job_id: int, artifacts: dict):
        self.job_id = job_id
        self.artifacts = artifacts

    def read_artifact(self, name: str):
        return self.artifacts.get(name)

    def get_stage_status(self, _name: str):
        return None

    def is_stage_complete(self, _name: str):
        return False


def _artifacts(input_data: dict) -> dict:
    return {
        "input": input_data,
        "parsed_identity": {"artist": "Artist", "title": "Track"},
        "organized": {
            "new_folder": "library/Artist/Track", "new_file": "library/Artist/Track/Track.mp4",
            "file_size_bytes": 123, "resolution_label": "1080p",
        },
        "ffprobe": {"width": 1920, "height": 1080, "video_codec": "h264"},
        "scraper_results": {
            "artist": "Artist", "title": "Track", "album": "Album", "year": 2026,
            "mb_artist_id": "artist-mbid", "mb_recording_id": "recording-mbid",
        },
        "version_detection": {"version_type": "normal"},
        "entity_resolution": {}, "source_links": {}, "artwork_results": {},
    }


def test_url_and_disk_sources_share_plan_builder_and_logical_output():
    url = build_plan_from_workspace(ArtifactWorkspace(1, _artifacts({
        "import_type": "url", "mode": "advanced", "options": {},
    })))
    disk = build_plan_from_workspace(ArtifactWorkspace(2, _artifacts({
        "import_type": "library", "mode": "advanced", "options": {},
    })))
    for key in ("video", "quality_signature", "genres", "entities", "version_type"):
        assert url[key] == disk[key], key
    assert url["import_type"] == "url" and disk["import_type"] == "library"

    app_root = Path(__file__).resolve().parents[1] / "app"
    url_source = (app_root / "pipeline_url" / "stages.py").read_text("utf-8")
    disk_source = (app_root / "pipeline_lib" / "stages.py").read_text("utf-8")
    for source in (url_source, disk_source):
        assert "from app.pipeline_lib.mutation_plan import build_plan_from_workspace" in source
        assert "from app.pipeline_lib.db_apply import apply_mutation_plan" in source
        assert "from app.pipeline_url.deferred import dispatch_deferred" in source


def test_unchanged_input_resumes_checkpoint_and_changed_policy_resets(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_module, "_WORKSPACE_ROOT", str(tmp_path))
    events = []
    monkeypatch.setattr(
        "app.services.stage_events.append_stage_event",
        lambda *args, **kwargs: events.append((args, kwargs)),
    )
    ws = ImportWorkspace(41)
    policy = {"import_type": "url", "url": "https://example.test/v", "policy": {"ai": False}}
    assert ws.prepare_run(policy) is False
    ws.write_artifact("download", {"path": "fixture.mp4"})
    ws.update_stage("download", "running")
    ws.update_stage("download", "complete")

    assert ws.prepare_run(policy) is True
    assert ws.is_stage_complete("download")
    assert ws.read_artifact("download")["path"] == "fixture.mp4"
    assert [event[0][2] for event in events] == ["running", "complete"]
    assert events[-1][1]["input_hash"].startswith("sha256:")
    assert events[-1][1]["duration_ms"] is not None

    changed = {**policy, "policy": {"ai": True}}
    assert ws.prepare_run(changed) is False
    assert not ws.has_artifact("download")
    assert not ws.is_stage_complete("download")
