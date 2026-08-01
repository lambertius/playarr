"""Canonical UI classification for every durable processing job."""
from __future__ import annotations

from collections.abc import Iterable


TERMINAL_STATUS_GROUPS = {"complete", "failed", "cancelled", "skipped"}

# A job type is classified here, once, instead of in each client. Prefix rules
# cover families whose concrete action suffixes can grow over time.
JOB_CATEGORY_RULES: dict[str, dict[str, tuple[str, ...]]] = {
    "download": {
        "types": ("import_url", "playlist_import", "redownload"),
        "prefixes": (),
    },
    "import": {
        "types": ("library_scan", "library_import", "library_import_video"),
        "prefixes": (),
    },
    "video_editor": {
        "types": (),
        "prefixes": ("video_editor_",),
    },
    "scraper": {
        "types": (
            "metadata_refresh", "batch_metadata_refresh", "metadata_scrape",
            "kodi_export", "rescan", "batch_rescan", "normalize",
            "batch_normalize", "scan_sources", "batch_scan_sources",
        ),
        "prefixes": (),
    },
}


def status_group(status: object) -> str:
    value = getattr(status, "value", status)
    normalized = str(value or "").casefold()
    return normalized if normalized in TERMINAL_STATUS_GROUPS else "active"


def job_category(job_type: str | None) -> str:
    normalized = (job_type or "").casefold()
    for category, rule in JOB_CATEGORY_RULES.items():
        if normalized in rule["types"] or any(
            normalized.startswith(prefix) for prefix in rule["prefixes"]
        ):
            return category
    return "system"


def category_rule(category: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if category == "system":
        known_types: list[str] = []
        known_prefixes: list[str] = []
        for rule in JOB_CATEGORY_RULES.values():
            known_types.extend(rule["types"])
            known_prefixes.extend(rule["prefixes"])
        return tuple(known_types), tuple(known_prefixes)
    rule = JOB_CATEGORY_RULES.get(category)
    if not rule:
        return (), ()
    return rule["types"], rule["prefixes"]


def known_categories() -> Iterable[str]:
    return (*JOB_CATEGORY_RULES.keys(), "system")
