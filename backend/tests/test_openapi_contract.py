"""API route-shape regression coverage for static paths and retired surfaces."""
from app.main import app


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


def test_kodi_addon_and_export_routes_are_retired():
    paths = app.openapi()["paths"]
    assert not [path for path in paths if "kodi" in path.lower()]
