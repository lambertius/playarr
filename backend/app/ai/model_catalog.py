"""
Model Catalog — Dynamic model discovery and caching per AI provider.

Provides:
- Curated model lists for OpenAI, Gemini, and Anthropic
- Live model discovery for Ollama (local)
- Caching with configurable TTL (default 1 hour)
- Tier-based model classification (fast / standard / high)
- Recommended model defaults per provider

The catalog is the single source of truth for what models are available.
Hardcoded lists are treated as curated allowlists. The OpenAI/Gemini/Anthropic
APIs return many internal or deprecated model IDs; we filter to the ones that
are useful for metadata enrichment tasks. Ollama models are discovered live
from the local server.

To add a new model, update the CURATED_MODELS dict below and optionally
update DEFAULT_TIERS if it should be a tier default.
"""
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ModelInfo:
    """A single model entry in the catalog."""
    id: str
    label: str
    tier: str  # "fast", "standard", "high"
    capabilities: List[str] = field(default_factory=lambda: ["text"])
    recommended_for: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProviderCatalog:
    """Full catalog response for a single provider."""
    provider: str
    models: List[ModelInfo]
    defaults: Dict  # { manual_default, auto_tiers: {fast, standard, high} }
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "models": [m.to_dict() for m in self.models],
            "defaults": self.defaults,
            "updated_at": self.updated_at,
        }


# ---------------------------------------------------------------------------
# Curated model lists (allowlists)
# Updated: 2026-03-05
# ---------------------------------------------------------------------------

CURATED_MODELS: Dict[str, List[ModelInfo]] = {
    "openai": [
        # GPT-5 family (current generation, reasoning models)
        ModelInfo("gpt-5", "GPT-5 (highest quality)", "high",
                  ["text", "vision", "reasoning"], ["correction", "hard_corrections", "metadata"]),
        ModelInfo("gpt-5-mini", "GPT-5 Mini (fast/cheap)", "standard",
                  ["text", "vision", "reasoning"], ["metadata", "verification", "correction"]),
        ModelInfo("gpt-5-nano", "GPT-5 Nano (bulk/cheap)", "fast",
                  ["text", "vision"], ["classification", "verification", "scene_ranking"]),
        # GPT-4.1 family (previous generation, fallback)
        ModelInfo("gpt-4.1", "GPT-4.1 (previous gen)", "high",
                  ["text", "vision"], ["correction", "hard_corrections", "metadata"]),
        ModelInfo("gpt-4.1-mini", "GPT-4.1 Mini (previous gen, fast)", "standard",
                  ["text", "vision"], ["metadata", "verification", "correction", "scene_ranking"]),
        ModelInfo("gpt-4.1-nano", "GPT-4.1 Nano (previous gen, cheapest)", "fast",
                  ["text"], ["classification", "verification"]),
        # Reasoning models (o-series)
        ModelInfo("o3-mini", "o3-mini (reasoning)", "high",
                  ["text", "reasoning"], ["correction", "hard_corrections"]),
        ModelInfo("o4-mini", "o4-mini (reasoning)", "high",
                  ["text", "reasoning"], ["correction", "hard_corrections"]),
    ],
    "gemini": [
        ModelInfo("gemini-2.0-flash", "Gemini 2.0 Flash (fast)", "fast",
                  ["text", "vision"], ["metadata", "verification", "scene_ranking"]),
        ModelInfo("gemini-2.0-flash-lite", "Gemini 2.0 Flash Lite (cheapest)", "fast",
                  ["text"], ["classification"]),
        ModelInfo("gemini-2.5-flash-preview-05-20", "Gemini 2.5 Flash Preview (standard)", "standard",
                  ["text", "vision"], ["metadata", "correction"]),
        ModelInfo("gemini-2.5-pro-preview-06-05", "Gemini 2.5 Pro Preview (high)", "high",
                  ["text", "vision"], ["correction", "hard_corrections"]),
    ],
    "claude": [
        ModelInfo("claude-haiku-3", "Claude 3 Haiku (fast/cheap)", "fast",
                  ["text"], ["classification", "verification"]),
        ModelInfo("claude-haiku-3.5-20241022", "Claude 3.5 Haiku (fast)", "fast",
                  ["text", "vision"], ["metadata", "verification", "scene_ranking"]),
        ModelInfo("claude-sonnet-4-20250514", "Claude Sonnet 4 (standard)", "standard",
                  ["text", "vision"], ["metadata", "correction"]),
        ModelInfo("claude-opus-4-20250514", "Claude Opus 4 (high)", "high",
                  ["text", "vision"], ["correction", "hard_corrections"]),
    ],
}

# Default tier mapping per provider: which model ID to use for each tier.
DEFAULT_TIERS: Dict[str, Dict[str, str]] = {
    "openai": {
        "fast": "gpt-5-nano",
        "standard": "gpt-5-mini",
        "high": "gpt-5",
    },
    "gemini": {
        "fast": "gemini-2.0-flash",
        "standard": "gemini-2.5-flash-preview-05-20",
        "high": "gemini-2.5-pro-preview-06-05",
    },
    "claude": {
        "fast": "claude-haiku-3.5-20241022",
        "standard": "claude-sonnet-4-20250514",
        "high": "claude-opus-4-20250514",
    },
}

# Manual mode default model per provider (a good all-rounder).
MANUAL_DEFAULTS: Dict[str, str] = {
    "openai": "gpt-5-mini",
    "gemini": "gemini-2.0-flash",
    "claude": "claude-sonnet-4-20250514",
}


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_catalog_cache: Dict[str, tuple] = {}  # provider -> (ProviderCatalog, timestamp)
CACHE_TTL = 3600  # 1 hour


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Catalog builders
# ---------------------------------------------------------------------------

def _build_curated_catalog(provider: str) -> ProviderCatalog:
    """Build a catalog from the curated allowlist."""
    models = CURATED_MODELS.get(provider, [])
    tiers = DEFAULT_TIERS.get(provider, {})
    manual_default = MANUAL_DEFAULTS.get(provider, models[0].id if models else "")
    return ProviderCatalog(
        provider=provider,
        models=models,
        defaults={
            "manual_default": manual_default,
            "auto_tiers": tiers,
        },
        updated_at=_iso_now(),
    )


def _discover_ollama_models(base_url: str) -> ProviderCatalog:
    """
    Query Ollama /api/tags to discover locally installed models.
    
    Returns a catalog where every model is tier="standard" since
    all local models are equivalent from a routing perspective.
    """
    models: List[ModelInfo] = []
    try:
        # Ollama API: GET /api/tags
        # The base_url from settings is typically http://localhost:11434/v1
        # but the Ollama native API is at /api/tags (no /v1 prefix).
        api_base = base_url.rstrip("/")
        if api_base.endswith("/v1"):
            api_base = api_base[:-3]

        resp = httpx.get(f"{api_base}/api/tags", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()

        for entry in data.get("models", []):
            name = entry.get("name", "")
            if not name:
                continue
            # Strip tag suffix for display (e.g. "llama3:latest" → "llama3")
            display = name.split(":")[0] if ":" in name else name
            size_gb = (entry.get("size", 0) or 0) / (1024**3)
            size_label = f" ({size_gb:.1f} GB)" if size_gb > 0 else ""
            models.append(ModelInfo(
                id=name,
                label=f"{display}{size_label}",
                tier="standard",
                capabilities=["text"],
                recommended_for=["metadata", "verification"],
            ))
    except Exception as e:
        logger.warning(f"Failed to discover Ollama models at {base_url}: {e}")

    # If discovery failed, return a placeholder
    if not models:
        models = [
            ModelInfo("llama3", "Llama 3 (default)", "standard", ["text"], ["metadata"]),
        ]

    manual_default = models[0].id if models else "llama3"
    return ProviderCatalog(
        provider="local",
        models=models,
        defaults={
            "manual_default": manual_default,
            "auto_tiers": {
                "fast": manual_default,
                "standard": manual_default,
                "high": manual_default,
            },
        },
        updated_at=_iso_now(),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_model_catalog(
    provider: str,
    local_base_url: str = "http://localhost:11434/v1",
    force_refresh: bool = False,
) -> ProviderCatalog:
    """
    Get the model catalog for a provider.

    Results are cached for CACHE_TTL seconds. For Ollama, discovery is
    re-run on cache miss or force_refresh.

    Args:
        provider: "openai", "gemini", "claude", "local"
        local_base_url: Ollama base URL (only used for provider="local")
        force_refresh: Bypass cache

    Returns:
        ProviderCatalog with models, defaults, and tier mappings.
    """
    global _catalog_cache

    now = time.time()
    cache_key = provider

    if not force_refresh and cache_key in _catalog_cache:
        catalog, ts = _catalog_cache[cache_key]
        if now - ts < CACHE_TTL:
            return catalog

    if provider == "local":
        catalog = _discover_ollama_models(local_base_url)
    elif provider in CURATED_MODELS:
        catalog = _build_curated_catalog(provider)
    else:
        # Unknown provider — return empty
        catalog = ProviderCatalog(
            provider=provider,
            models=[],
            defaults={"manual_default": "", "auto_tiers": {}},
            updated_at=_iso_now(),
        )

    _catalog_cache[cache_key] = (catalog, now)
    return catalog


def validate_model_id(provider: str, model_id: str, local_base_url: str = "http://localhost:11434/v1") -> bool:
    """Check if a model ID is valid for the given provider."""
    catalog = get_model_catalog(provider, local_base_url)
    return any(m.id == model_id for m in catalog.models)


def get_default_model(provider: str) -> str:
    """Get the manual-mode default model for a provider."""
    return MANUAL_DEFAULTS.get(provider, "")


def get_tier_model(provider: str, tier: str) -> str:
    """Get the model for a given tier. Falls back to 'standard' then 'fast'."""
    tiers = DEFAULT_TIERS.get(provider, {})
    return tiers.get(tier) or tiers.get("standard") or tiers.get("fast", "")


# ---------------------------------------------------------------------------
# Model availability testing
# ---------------------------------------------------------------------------

@dataclass
class ModelTestResult:
    """Result of testing a single model's availability."""
    model_id: str
    available: bool
    error: str = ""
    response_time_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "available": self.available,
            "error": self.error,
            "response_time_ms": self.response_time_ms,
        }


# Cache: provider -> (results_dict, timestamp)
_availability_cache: Dict[str, tuple] = {}
AVAILABILITY_CACHE_TTL = 3600  # 1 hour


def get_cached_availability(provider: str) -> Optional[Dict[str, ModelTestResult]]:
    """Return cached availability results if fresh, else None."""
    if provider in _availability_cache:
        results, ts = _availability_cache[provider]
        if time.time() - ts < AVAILABILITY_CACHE_TTL:
            return results
    return None


def test_model_availability(
    provider: str,
    api_key: str,
    model_ids: Optional[List[str]] = None,
    base_url: Optional[str] = None,
    force: bool = False,
) -> Dict[str, ModelTestResult]:
    """
    Test which models are actually accessible with the given API key.

    Sends a minimal request to each model and records success/failure.
    Results are cached for AVAILABILITY_CACHE_TTL seconds.

    Args:
        provider: "openai", "gemini", "claude", "local"
        api_key: API key for the provider
        model_ids: Optional list of model IDs to test (defaults to all curated)
        base_url: Base URL for local provider
        force: Bypass cache

    Returns:
        Dict mapping model_id -> ModelTestResult
    """
    if not force:
        cached = get_cached_availability(provider)
        if cached is not None:
            return cached

    if model_ids is None:
        catalog = get_model_catalog(provider, local_base_url=base_url or "http://localhost:11434/v1")
        model_ids = [m.id for m in catalog.models]

    results: Dict[str, ModelTestResult] = {}

    for model_id in model_ids:
        result = _test_single_model(provider, api_key, model_id, base_url)
        results[model_id] = result
        logger.info(
            f"Model test: {provider}/{model_id} -> "
            f"{'OK' if result.available else 'FAIL'}"
            f"{(' (' + result.error + ')') if result.error else ''}"
            f" ({result.response_time_ms}ms)"
        )

    _availability_cache[provider] = (results, time.time())
    return results


def _test_single_model(
    provider: str,
    api_key: str,
    model_id: str,
    base_url: Optional[str] = None,
) -> ModelTestResult:
    """Send a minimal test request to a single model."""
    import time as _time

    t0 = _time.monotonic()
    try:
        if provider == "openai":
            payload: dict = {
                "model": model_id,
                "messages": [{"role": "user", "content": "Say OK"}],
                "max_completion_tokens": 16,
            }
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
        elif provider == "gemini":
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model_id}:generateContent?key={api_key}"
            )
            payload = {"contents": [{"parts": [{"text": "Say OK"}]}]}
            resp = httpx.post(url, json=payload, timeout=30)
        elif provider == "claude":
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                json={
                    "model": model_id,
                    "max_tokens": 16,
                    "messages": [{"role": "user", "content": "Say OK"}],
                },
                headers={
                    "x-api-key": api_key,
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                },
                timeout=30,
            )
        elif provider == "local":
            base = (base_url or "http://localhost:11434/v1").rstrip("/")
            resp = httpx.post(
                f"{base}/chat/completions",
                json={
                    "model": model_id,
                    "messages": [{"role": "user", "content": "Say OK"}],
                },
                timeout=30,
            )
        else:
            return ModelTestResult(model_id=model_id, available=False, error=f"Unknown provider: {provider}")

        elapsed = int((_time.monotonic() - t0) * 1000)

        if resp.status_code == 200:
            return ModelTestResult(model_id=model_id, available=True, response_time_ms=elapsed)

        # Parse error
        err_msg = ""
        try:
            err_body = resp.json()
            err_msg = err_body.get("error", {}).get("message", "") or str(resp.status_code)
        except Exception:
            err_msg = f"HTTP {resp.status_code}"

        return ModelTestResult(
            model_id=model_id,
            available=False,
            error=err_msg[:200],
            response_time_ms=elapsed,
        )

    except Exception as e:
        elapsed = int((_time.monotonic() - t0) * 1000)
        return ModelTestResult(
            model_id=model_id,
            available=False,
            error=str(e)[:200],
            response_time_ms=elapsed,
        )
