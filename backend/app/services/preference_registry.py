"""Typed, versioned definitions for server-backed UI preferences."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable


Validator = Callable[[Any], bool]


@dataclass(frozen=True)
class PreferenceDefinition:
    name: str
    schema_version: int
    scope: str
    defaults: dict[str, Any]
    validators: dict[str, Validator]

    def validate_patch(self, patch: dict[str, Any]) -> None:
        unknown = sorted(set(patch) - set(self.validators))
        if unknown:
            raise ValueError(f"Unknown preference field(s): {', '.join(unknown)}")
        invalid = [key for key, value in patch.items() if not self.validators[key](value)]
        if invalid:
            raise ValueError(f"Invalid value for preference field(s): {', '.join(invalid)}")

    def merged(self, current: Any, patch: dict[str, Any]) -> dict[str, Any]:
        value = deepcopy(self.defaults)
        if isinstance(current, dict):
            value.update({key: val for key, val in current.items() if key in self.validators})
        value.update(patch)
        return value

    def catalogue(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scope": self.scope,
            "defaults": deepcopy(self.defaults),
            "fields": sorted(self.validators),
        }


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nullable_int(value: Any) -> bool:
    return value is None or _is_int(value)


def _nullable_str(value: Any) -> bool:
    return value is None or _is_str(value)


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _string_map(value: Any) -> bool:
    return isinstance(value, dict) and all(isinstance(key, str) and isinstance(item, str) for key, item in value.items())


def _int_map(value: Any) -> bool:
    return isinstance(value, dict) and all(isinstance(key, str) and _is_int(item) for key, item in value.items())


def _enum(*values: str) -> Validator:
    return lambda value: value in values


def _int_range(low: int, high: int) -> Validator:
    return lambda value: _is_int(value) and low <= value <= high


def _number_range(low: float, high: float) -> Validator:
    return lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high


def _one_of(*validators: Validator) -> Validator:
    return lambda value: any(validator(value) for validator in validators)


REGISTRY: dict[str, PreferenceDefinition] = {}


def _register(name: str, defaults: dict[str, Any], validators: dict[str, Validator], *, scope: str = "instance") -> None:
    REGISTRY[name] = PreferenceDefinition(name, 1, scope, defaults, validators)


_register("library", {"view": "grid", "sort": "artist", "dir": "asc", "pageSize": 50}, {
    "view": _enum("grid", "list"), "sort": _is_str, "dir": _enum("asc", "desc"),
    "pageSize": _int_range(10, 500),
})
_register("queue-v2", {"status": "active", "category": "all", "pageSize": 50}, {
    "status": _enum("active", "complete", "failed", "cancelled", "skipped"),
    "category": _is_str, "pageSize": _int_range(10, 200),
})
_register("review", {"categoryFilter": "all", "pageSize": 25}, {
    "categoryFilter": _nullable_str, "pageSize": _int_range(0, 1000),
})
_register("archive", {"pageSize": 25, "view": "list"}, {
    "pageSize": _int_range(10, 200), "view": _enum("grid", "list"),
})
_register("panels", {
    "thumbnailsExpanded": False, "trackHistoryExpanded": False,
    "navViews": {}, "navSorts": {}, "navDirections": {}, "navPageSizes": {},
}, {
    "thumbnailsExpanded": _is_bool, "trackHistoryExpanded": _is_bool,
    "navViews": _string_map, "navSorts": _string_map,
    "navDirections": _string_map, "navPageSizes": _int_map,
})
_register("partyExclusions", {
    "version_types": [], "artists": [], "genres": [], "albums": [],
    "min_song_rating": None, "min_video_rating": None, "exclude_adult": True,
}, {
    "version_types": _string_list, "artists": _string_list, "genres": _string_list,
    "albums": _string_list, "min_song_rating": _nullable_int,
    "min_video_rating": _nullable_int, "exclude_adult": _is_bool,
})
_register("partyAnimation", {"enabled": True, "duration": 8}, {
    "enabled": _is_bool, "duration": _int_range(5, 15),
})
_register("partyEra", {"enabled": False, "year": 2026}, {
    "enabled": _is_bool, "year": _int_range(1900, 2200),
})
_register("partyPlaylist", {"playlistId": None}, {
    "playlistId": _nullable_int,
})
_register("artwork", {
    "artworkSize": 150, "scrollDuration": 60, "changeRate": 4,
    "fadeDuration": 1, "playbackRatio": 75, "queueOpacity": 70,
    "overlayDuration": 30, "artRepeatPenalty": 50, "overlaySize": 35,
    "queueClock": False, "artChangeEnabled": True, "artChangeCount": 1,
    "artChangeStyle": "fade", "queueHideMode": "off", "queueHideDelay": 5,
    "tvResolution": 1080, "tvTranscode": False, "browserTranscode": False,
    "castTranscode": False,
}, {
    "artworkSize": _int_range(50, 1000), "scrollDuration": _int_range(1, 600),
    "changeRate": _number_range(0.1, 300), "fadeDuration": _number_range(0, 30),
    "playbackRatio": _int_range(0, 100), "queueOpacity": _int_range(0, 100),
    "overlayDuration": _int_range(0, 600), "artRepeatPenalty": _int_range(0, 100),
    "overlaySize": _int_range(10, 100), "queueClock": _is_bool,
    "artChangeEnabled": _is_bool, "artChangeCount": _int_range(1, 20),
    "artChangeStyle": _is_str, "queueHideMode": _is_str,
    "queueHideDelay": _int_range(0, 120), "tvResolution": _int_range(240, 4320),
    "tvTranscode": _is_bool, "browserTranscode": _is_bool, "castTranscode": _is_bool,
})


def get_preference_definition(name: str) -> PreferenceDefinition:
    try:
        return REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"Unknown preference group: {name}") from exc


def preference_catalogue() -> dict[str, dict[str, Any]]:
    return {name: definition.catalogue() for name, definition in sorted(REGISTRY.items())}
