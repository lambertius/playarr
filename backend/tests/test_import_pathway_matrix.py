from itertools import product

import pytest

from app.pipeline.import_context import ImportContext, selected_stages
from app.services.import_policy import ImportPolicy


PATHWAYS = (
    "url_add", "playlist_add", "disk_import", "rescan",
    "metadata_action", "trusted_sidecar",
)
MODES = (
    "existing_only", "wiki_only", "musicbrainz_only",
    "scrapers", "ai_proofread", "ai_only",
)


def _policy(mode: str) -> ImportPolicy:
    providers = {
        "wiki_only": ("wikipedia",),
        "musicbrainz_only": ("musicbrainz",),
        "scrapers": ("wikipedia", "musicbrainz"),
        "ai_proofread": ("wikipedia", "musicbrainz"),
    }.get(mode, ())
    ai_role = {"ai_proofread": "proofread", "ai_only": "ai_only"}.get(mode, "disabled")
    return ImportPolicy(metadata_mode=mode, providers=providers, ai_role=ai_role)


@pytest.mark.parametrize("pathway,mode", list(product(PATHWAYS, MODES)))
def test_every_pathway_and_policy_has_a_deterministic_stage_contract(pathway, mode):
    context = ImportContext(pathway=pathway, source="fixture", policy=_policy(mode), dry_run=True)
    stages = selected_stages(context)
    assert stages[0] == "identity"
    assert len(stages) == len(set(stages))
    assert ("metadata_resolution" in stages) is (mode != "existing_only")
    assert ("trusted_sidecar" in stages) is (pathway == "trusted_sidecar")


def test_dry_run_and_production_select_identical_stages():
    policy = _policy("ai_proofread")
    production = ImportContext(pathway="url_add", source="fixture", policy=policy)
    dry_run = production.model_copy(update={"dry_run": True})
    assert selected_stages(production) == selected_stages(dry_run)
