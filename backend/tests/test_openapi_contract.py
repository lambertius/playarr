"""API route-shape regression coverage for static paths and retired surfaces."""
from pathlib import Path

from starlette.routing import Match

from app.main import app


def _matched_leaf_path(route, scope: dict) -> str:
    """Return the concrete path for both eager and lazy FastAPI routers."""
    direct_path = getattr(route, "path", None)
    if direct_path:
        return direct_path
    original_router = getattr(route, "original_router", None)
    if original_router is None:
        return ""
    leaves = [
        leaf for leaf in original_router.routes
        if leaf.matches(scope)[0] is Match.FULL
    ]
    return getattr(leaves[0], "path", "") if leaves else ""


def test_openapi_builds_and_static_command_routes_are_not_shadowed():
    schema = app.openapi()
    paths = schema["paths"]

    assert "/api/resolve/batch" in paths
    assert "/api/review/scan-enrichment" in paths
    assert "/api/review/scan-artwork" in paths
    assert "/api/review/scan-renames" in paths
    assert "/api/search/artist" in paths
    assert "/api/search/recording" in paths
    assert "/api/search/release" in paths

    for path, method in (
        ("/api/resolve/batch", "POST"),
        ("/api/review/scan-enrichment", "POST"),
        ("/api/review/scan-artwork", "POST"),
        ("/api/review/scan-renames", "POST"),
        ("/api/search/artist", "GET"),
        ("/api/search/recording", "GET"),
        ("/api/search/release", "GET"),
    ):
        scope = {"type": "http", "path": path, "method": method, "root_path": ""}
        matches = [route for route in app.routes if route.matches(scope)[0] is Match.FULL]
        assert matches, f"{method} {path} was unreachable"
        assert _matched_leaf_path(matches[0], scope) == path

    assert "/api/resolve/videos/{video_id}" in paths
    assert "/api/resolve/{video_id}" not in paths


def test_kodi_addon_and_export_routes_are_retired():
    paths = app.openapi()["paths"]
    assert not [path for path in paths if "kodi" in path.lower()]

    root = Path(__file__).parents[2]
    scanned = [
        root / "playarr.spec",
        root / "backend/app/tasks.py",
        root / "backend/app/routers/settings.py",
        root / "backend/app/routers/metadata.py",
        root / "backend/app/routers/resolve.py",
        root / "frontend/src/lib/api.ts",
        root / "frontend/src/hooks/queries.ts",
        root / "frontend/src/pages/SettingsPage.tsx",
    ]
    forbidden = (
        "app.metadata.exporters.kodi", "plugin.video.playarr", "/kodi",
        "KodiPluginSettings", "ExportKodi", "kodiPluginInfo", "kodi_export_task",
    )
    violations = {
        str(path.relative_to(root)): [token for token in forbidden if token in path.read_text("utf-8")]
        for path in scanned
        if any(token in path.read_text("utf-8") for token in forbidden)
    }
    assert not violations, violations
    assert not (root / "backend/app/metadata/exporters/kodi.py").exists()
    assert not (root / "kodi/plugin.video.playarr-1.9.18.zip").exists()
    assert (root / "docs/KODI_REMOVAL.md").is_file()
