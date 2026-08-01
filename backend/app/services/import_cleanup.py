"""Rollback Stage-B files that were never committed to the library DB."""
from __future__ import annotations

import os


def cleanup_import_artifacts(workspace) -> None:
    organized = workspace.read_artifact("organized") or {}
    paths = []
    if organized.get("new_file"):
        paths.append(organized["new_file"])
    for artifact_name in ("artwork_source", "artwork_results"):
        artifact = workspace.read_artifact(artifact_name) or {}
        paths.extend(
            asset["file_path"] for asset in artifact.get("assets", [])
            if asset.get("file_path")
        )
    folder = organized.get("new_folder")
    if folder and os.path.isdir(folder):
        paths.extend(
            os.path.join(folder, name) for name in os.listdir(folder)
            if name.endswith(".nfo")
        )
    for path in dict.fromkeys(paths):
        if os.path.isfile(path):
            try:
                os.remove(path)
                workspace.log(f"Cleaned up uncommitted artifact: {os.path.basename(path)}")
            except OSError as exc:
                workspace.log(f"Cleanup waiting for release: {path}: {exc}", level="warning")
    if folder and os.path.isdir(folder):
        try:
            os.rmdir(folder)
        except OSError:
            pass
