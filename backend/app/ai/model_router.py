"""
Model Router — Automatic model selection based on task type and complexity.

Implements intelligent model routing that:
- Uses the model catalog as the single source of truth for available models
- Selects the cheapest sufficient model for each task
- Escalates to larger models on low confidence or retry
- Supports per-provider routing rules via tiers
- Allows manual override via settings (default model + optional per-task)

Task Types:
- classification  — quick mismatch detection / genre tagging
- enrichment      — standard metadata enrichment
- correction      — complex correction after mismatch detected
- scene_ranking   — ranking thumbnail candidates
- verification    — verifying existing metadata accuracy
- fallback        — retry with stronger model after failure

Tier Mapping (which task uses which tier):
┌──────────────┬────────────┐
│ Task         │ Tier       │
├──────────────┼────────────┤
│ classify     │ fast       │
│ enrichment   │ standard   │
│ correction   │ high       │
│ scene_rank   │ fast       │
│ verification │ fast       │
│ fallback     │ high       │
└──────────────┴────────────┘
"""
import logging
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task types
# ---------------------------------------------------------------------------

class TaskType:
    CLASSIFICATION = "classification"
    ENRICHMENT = "enrichment"
    CORRECTION = "correction"
    SCENE_RANKING = "scene_ranking"
    VERIFICATION = "verification"
    FALLBACK = "fallback"


# Task → default tier mapping
TASK_TIER_MAP: Dict[str, str] = {
    TaskType.CLASSIFICATION: "fast",
    TaskType.ENRICHMENT: "standard",
    TaskType.CORRECTION: "high",
    TaskType.SCENE_RANKING: "fast",
    TaskType.VERIFICATION: "fast",
    TaskType.FALLBACK: "high",
}

TIER_ORDER = ["fast", "standard", "high"]


# ---------------------------------------------------------------------------
# Model Router
# ---------------------------------------------------------------------------

@dataclass
class ModelSelection:
    """Result of model selection."""
    model: str
    task_type: str
    provider: str
    escalated: bool = False
    reason: str = ""


class ModelRouter:
    """
    Routes tasks to the appropriate model based on provider, task type,
    and complexity signals.

    Supports two modes:
    - auto: Uses tier-based routing from the model catalog with escalation
    - manual: Uses user-specified default model (with optional per-task overrides)
    """

    def __init__(
        self,
        provider: str,
        mode: str = "auto",
        manual_default: Optional[str] = None,
        manual_fallback: Optional[str] = None,
        manual_overrides: Optional[Dict[str, str]] = None,
        tier_preference: str = "balanced",
    ):
        self.provider = provider
        self.mode = mode
        self.manual_default = manual_default
        self.manual_fallback = manual_fallback
        self.manual_overrides = manual_overrides or {}
        self.tier_preference = tier_preference

    def select_model(
        self,
        task_type: str,
        prompt_tokens: int = 0,
        confidence: Optional[float] = None,
        is_retry: bool = False,
        mismatch_score: float = 0.0,
    ) -> ModelSelection:
        """Select the best model for a task."""
        if self.mode == "manual":
            return self._manual_select(task_type)
        return self._auto_select(
            task_type, prompt_tokens, confidence, is_retry, mismatch_score,
        )

    def _manual_select(self, task_type: str) -> ModelSelection:
        """Use user-specified model for the task type."""
        model = self.manual_overrides.get(task_type)
        if model:
            return ModelSelection(
                model=model,
                task_type=task_type,
                provider=self.provider,
                reason=f"manual override for {task_type}",
            )

        model = self.manual_default
        if not model:
            from app.ai.model_catalog import get_default_model
            model = get_default_model(self.provider)

        if not model:
            return self._auto_select(task_type, 0, None, False, 0.0)

        return ModelSelection(
            model=model,
            task_type=task_type,
            provider=self.provider,
            reason="manual default",
        )

    def _auto_select(
        self,
        task_type: str,
        prompt_tokens: int,
        confidence: Optional[float],
        is_retry: bool,
        mismatch_score: float,
    ) -> ModelSelection:
        """Automatically select model based on tier mapping and heuristics."""
        from app.ai.model_catalog import get_tier_model

        if self.provider == "local":
            return ModelSelection(
                model="configured",
                task_type=task_type,
                provider=self.provider,
                reason="local provider uses configured model",
            )

        base_tier = TASK_TIER_MAP.get(task_type, "standard")
        base_tier = self._apply_preference(base_tier)

        base_model = get_tier_model(self.provider, base_tier)
        high_model = get_tier_model(self.provider, "high")

        if not base_model:
            return ModelSelection(
                model="unknown",
                task_type=task_type,
                provider=self.provider,
                reason="no model found in catalog",
            )

        should_escalate = False
        reason = f"auto: {task_type} → {base_tier} tier"

        if is_retry:
            should_escalate = True
            reason = "escalated: retry after failure"
        elif confidence is not None and confidence < 0.5:
            should_escalate = True
            reason = f"escalated: low confidence ({confidence:.2f})"
        elif mismatch_score > 0.6:
            should_escalate = True
            reason = f"escalated: high mismatch ({mismatch_score:.2f})"
        elif prompt_tokens > 2000:
            should_escalate = True
            reason = f"escalated: large prompt ({prompt_tokens} tokens)"

        if should_escalate and high_model and high_model != base_model:
            return ModelSelection(
                model=high_model,
                task_type=task_type,
                provider=self.provider,
                escalated=True,
                reason=reason,
            )

        return ModelSelection(
            model=base_model,
            task_type=task_type,
            provider=self.provider,
            reason=reason,
        )

    def _apply_preference(self, tier: str) -> str:
        """Shift tier based on user preference."""
        if self.tier_preference == "cheapest":
            idx = TIER_ORDER.index(tier) if tier in TIER_ORDER else 1
            return TIER_ORDER[max(0, idx - 1)]
        elif self.tier_preference == "accuracy":
            idx = TIER_ORDER.index(tier) if tier in TIER_ORDER else 1
            return TIER_ORDER[min(len(TIER_ORDER) - 1, idx + 1)]
        return tier

    def get_routing_preview(self) -> list:
        """Get a preview of what model would be selected for each task type."""
        from app.ai.model_catalog import get_model_catalog

        preview = []
        catalog = get_model_catalog(self.provider)
        model_labels = {m.id: m.label for m in catalog.models}

        tasks = [
            TaskType.ENRICHMENT,
            TaskType.VERIFICATION,
            TaskType.SCENE_RANKING,
            TaskType.FALLBACK,
        ]

        for task in tasks:
            selection = self.select_model(task)
            label = model_labels.get(selection.model, selection.model)
            preview.append({
                "task": task,
                "model_id": selection.model,
                "model_label": label,
                "reason": selection.reason,
            })
        return preview


def get_model_router(
    provider_name: Optional[str] = None,
    mode: Optional[str] = None,
) -> ModelRouter:
    """
    Create a ModelRouter from settings or explicit overrides.
    """
    from app.database import SessionLocal
    from app.models import AppSetting

    db = SessionLocal()
    try:
        if not provider_name:
            # Check DB first (settings UI saves here), then fall back to env config
            row = db.query(AppSetting).filter(
                AppSetting.key == "ai_provider",
                AppSetting.user_id.is_(None),
            ).first()
            if row and row.value and row.value != "none":
                provider_name = row.value
            else:
                from app.config import get_settings
                provider_name = get_settings().ai_provider

        if not provider_name or provider_name == "none":
            return ModelRouter(provider="none", mode="auto")

        if not mode:
            setting = db.query(AppSetting).filter(
                AppSetting.key == "ai_model_selection_mode",
                AppSetting.user_id.is_(None),
            ).first()
            mode = setting.value if setting else "auto"

        setting_keys = [
            "ai_model_default", "ai_model_fallback",
            "ai_model_metadata", "ai_model_verification", "ai_model_scene",
            "ai_auto_tier_preference",
        ]
        rows = db.query(AppSetting).filter(
            AppSetting.key.in_(setting_keys),
            AppSetting.user_id.is_(None),
        ).all()
        settings_map = {r.key: r.value for r in rows}

        manual_default = settings_map.get("ai_model_default")
        manual_fallback = settings_map.get("ai_model_fallback")
        tier_preference = settings_map.get("ai_auto_tier_preference", "balanced")

        manual_overrides = {}
        task_key_map = {
            "ai_model_metadata": TaskType.ENRICHMENT,
            "ai_model_verification": TaskType.VERIFICATION,
            "ai_model_scene": TaskType.SCENE_RANKING,
        }
        for key, task in task_key_map.items():
            val = settings_map.get(key)
            if val:
                manual_overrides[task] = val

        if manual_fallback:
            manual_overrides[TaskType.FALLBACK] = manual_fallback
            manual_overrides[TaskType.CORRECTION] = manual_fallback

        return ModelRouter(
            provider=provider_name,
            mode=mode,
            manual_default=manual_default,
            manual_fallback=manual_fallback,
            manual_overrides=manual_overrides,
            tier_preference=tier_preference,
        )
    finally:
        db.close()
