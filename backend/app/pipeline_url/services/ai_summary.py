# AUTO-SEPARATED from services/ai_summary.py for pipeline_url pipeline
# This file is independent — changes here do NOT affect the other pipeline.
"""
AI Summary Service — Plug-in provision for AI-generated descriptions.

Supports:
- Gemini: Give it a link and request a summary
- OpenAI/ChatGPT: Give it scraped text and request a Kodi-suitable description

API keys configured via environment variables or per-user settings.
"""
import logging
from typing import Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


def generate_ai_summary(
    text: str,
    source_url: Optional[str] = None,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    max_length: int = 2000,
) -> Optional[str]:
    """
    Generate an AI summary suitable for Kodi plot/description field.

    Args:
        text: The raw text to summarize (scraped content)
        source_url: Original video/page URL (used by Gemini)
        provider: "gemini" or "openai" (None = use settings default)
        api_key: Override API key (None = use settings/env)
        max_length: Target max character length for output

    Returns:
        Summarized text or None if AI is disabled/fails
    """
    settings = get_settings()

    if provider is None:
        provider = settings.ai_provider

    if provider == "none":
        logger.debug("AI provider set to 'none' — skipping AI summary")
        return None

    if provider == "gemini":
        key = api_key or settings.gemini_api_key
        if not key:
            logger.warning("Gemini API key not configured")
            return None
        return _gemini_summarize(key, text, source_url, max_length)

    elif provider == "openai":
        key = api_key or settings.openai_api_key
        if not key:
            logger.warning("OpenAI API key not configured")
            return None
        return _openai_summarize(key, text, max_length)

    else:
        logger.warning(f"Unknown AI provider: {provider}")
        return None


def _gemini_summarize(
    api_key: str,
    text: str,
    source_url: Optional[str],
    max_length: int,
) -> Optional[str]:
    """Generate summary using Google Gemini API."""
    try:
        prompt = (
            f"Improve the following music video description for use in a Kodi media library. "
            f"Keep the full detail and information from the original text. "
            f"Clean up any Wikipedia formatting artefacts, citation markers, or raw markup. "
            f"Do NOT compress or summarize — preserve the rich detail. "
            f"Maximum {max_length} characters. "
            f"If the text is already clean and informative, return it largely unchanged.\n\n"
        )
        if source_url:
            prompt += f"Source: {source_url}\n\n"
        prompt += f"Content:\n{text[:3000]}"

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={api_key}"

        resp = httpx.post(
            url,
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": max_length,
                    "temperature": 0.3,
                },
            },
            timeout=30,
        )

        if resp.status_code == 200:
            data = resp.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "").strip()

        logger.warning(f"Gemini API returned {resp.status_code}: {resp.text[:200]}")

    except Exception as e:
        logger.error(f"Gemini summary error: {e}")

    return None


def _openai_summarize(
    api_key: str,
    text: str,
    max_length: int,
) -> Optional[str]:
    """Generate summary using OpenAI ChatGPT API."""
    try:
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a media library assistant. "
                            "Polish and improve music video descriptions for Kodi. "
                            "Keep the full detail — do NOT summarize or compress."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Improve this music video description for a Kodi media library. "
                            f"Keep the full detail and information — do NOT summarize or compress. "
                            f"Clean up formatting artefacts. Maximum {max_length} characters:\n\n"
                            f"{text[:3000]}"
                        ),
                    },
                ],
                "max_tokens": max(max_length // 3, 500),
                "temperature": 0.3,
            },
            timeout=30,
        )

        if resp.status_code == 200:
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()

        logger.warning(f"OpenAI API returned {resp.status_code}: {resp.text[:200]}")

    except Exception as e:
        logger.error(f"OpenAI summary error: {e}")

    return None
