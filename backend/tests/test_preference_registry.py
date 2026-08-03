import pytest

from app.services.preference_registry import REGISTRY, get_preference_definition, preference_catalogue


def test_every_preference_default_is_typed_and_catalogued():
    catalogue = preference_catalogue()
    assert set(catalogue) == set(REGISTRY)
    for name, definition in REGISTRY.items():
        assert set(definition.defaults) == set(definition.validators), name
        definition.validate_patch(definition.defaults)
        assert catalogue[name]["fields"] == sorted(definition.defaults)


def test_settings_surface_preference_groups_are_registered():
    expected = {
        "artwork", "partyAnimation", "partyEra", "partyExclusions",
        "partyPlaylist", "library", "queue-v2", "review", "archive",
        "workspace", "panels",
    }
    assert expected <= set(REGISTRY)


def test_unknown_preference_fields_are_rejected():
    with pytest.raises(ValueError, match="Unknown preference field"):
        get_preference_definition("artwork").validate_patch({"unregistered": True})
