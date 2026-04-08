"""
Single source of truth for the Playarr application version.

Bump this value when cutting a new release.  The version is:
  • surfaced in the API via /api/version
  • written to the DB on startup (schema_version setting)
  • shown in Settings > System in the frontend
  • used as the FastAPI app.version
"""

APP_VERSION = "1.9.4"
