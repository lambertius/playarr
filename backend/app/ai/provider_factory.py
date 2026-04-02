"""
Provider Factory — Instantiate the configured AI provider.
"""
import logging
from typing import Optional

from app.ai.providers.base import AIProviderBase
from app.ai.providers.openai_provider import OpenAIProvider
from app.ai.providers.gemini_provider import GeminiProvider
from app.ai.providers.claude_provider import ClaudeProvider
from app.ai.providers.local_provider import LocalProvider

logger = logging.getLogger(__name__)


def _get_db_api_key(key_name: str) -> Optional[str]:
    """
    Read an API key from the AppSetting DB table.

    The UI saves API keys to the database, not to env vars / .env.
    This function bridges that gap so the provider factory can find
    keys saved via the settings panel.
    """
    try:
        from app.database import SessionLocal
        from app.models import AppSetting
        db = SessionLocal()
        try:
            row = db.query(AppSetting).filter(
                AppSetting.key == key_name,
                AppSetting.user_id.is_(None),
            ).first()
            return row.value if row else None
        finally:
            db.close()
    except Exception:
        return None


def get_ai_provider(
    provider_name: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Optional[AIProviderBase]:
    """
    Create an AI provider instance from settings or explicit overrides.

    API key resolution order:
      1. Explicit `api_key` parameter (e.g. from test-connection request)
      2. Database AppSetting table (saved via the settings UI)
      3. Environment variable / .env file (via pydantic-settings)

    Args:
        provider_name: "openai", "gemini", "claude", "local", or "none"
        api_key: API key override (falls back to DB then env)
        model: Model name override
        base_url: Base URL for local provider

    Returns:
        Configured AIProviderBase instance, or None if AI is disabled.
    """
    from app.config import get_settings
    settings = get_settings()

    name = provider_name or _get_db_api_key("ai_provider") or settings.ai_provider
    if name == "none" or not name:
        return None

    if name == "openai":
        key = api_key or _get_db_api_key("openai_api_key") or settings.openai_api_key
        if not key:
            logger.warning("OpenAI API key not configured")
            return None
        return OpenAIProvider(api_key=key, model=model or "gpt-5-mini")

    elif name == "gemini":
        key = api_key or _get_db_api_key("gemini_api_key") or settings.gemini_api_key
        if not key:
            logger.warning("Gemini API key not configured")
            return None
        return GeminiProvider(api_key=key, model=model or "gemini-2.0-flash")

    elif name == "claude":
        key = api_key or _get_db_api_key("claude_api_key") or getattr(settings, "claude_api_key", None)
        if not key:
            logger.warning("Claude API key not configured")
            return None
        return ClaudeProvider(api_key=key, model=model or "claude-sonnet-4-20250514")

    elif name == "local":
        db_url = _get_db_api_key("local_llm_base_url")
        db_model = _get_db_api_key("local_llm_model")
        url = base_url or db_url or getattr(settings, "local_llm_base_url", "http://localhost:11434/v1")
        mdl = model or db_model or getattr(settings, "local_llm_model", "llama3")
        return LocalProvider(base_url=url, model=mdl)

    else:
        logger.warning(f"Unknown AI provider: {name}")
        return None
