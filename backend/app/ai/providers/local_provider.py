"""
Local LLM Provider — Compatible with OpenAI-style APIs (Ollama, LM Studio, etc).
"""
import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.ai.providers.base import (
    AIProviderBase, AIMetadataResponse,
    PLOT_GENERATION_PROMPT,
)
from app.ai.prompt_builder import build_metadata_enrichment_prompt, SYSTEM_PROMPT
from app.ai.response_parser import parse_enrichment_response, extract_ai_metadata

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "llama3"
DEFAULT_BASE_URL = "http://localhost:11434/v1"


class LocalProvider(AIProviderBase):
    """
    Local LLM provider using OpenAI-compatible API.

    Works with:
    - Ollama (default: http://localhost:11434/v1)
    - LM Studio (http://localhost:1234/v1)
    - text-generation-webui (http://localhost:5000/v1)
    - Any OpenAI-compatible endpoint
    """

    name = "local"

    def __init__(self, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL,
                 api_key: str = "not-needed"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key

    def is_configured(self) -> bool:
        """Local providers are configured if we can reach the endpoint."""
        try:
            resp = httpx.get(f"{self.base_url}/models", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def get_model_name(self) -> str:
        return f"local/{self.model}"

    def _call_api(self, system_prompt: str, user_prompt: str, max_tokens: int = 800) -> dict:
        """Make an OpenAI-compatible chat completion call."""
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.3,
            },
            timeout=120,  # Local models can be slower
        )

        if resp.status_code != 200:
            logger.error(f"Local LLM API {resp.status_code}: {resp.text[:300]}")
            raise RuntimeError(f"Local LLM API error {resp.status_code}")

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        tokens = data.get("usage", {}).get("total_tokens", 0)

        return {"content": content, "tokens": tokens}

    def enrich_metadata(
        self,
        scraped: Dict[str, Any],
        video_filename: Optional[str] = None,
        source_url: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        channel_name: Optional[str] = None,
        upload_date: Optional[str] = None,
        mismatch_signals: Optional[List[Dict[str, Any]]] = None,
        fingerprint_hint: Optional[Dict[str, Any]] = None,
        review_description_only: bool = False,
        platform_description: Optional[str] = None,
        platform_tags: Optional[List[str]] = None,
        custom_system_prompt: Optional[str] = None,
        custom_enrichment_template: Optional[str] = None,
        custom_review_template: Optional[str] = None,
    ) -> AIMetadataResponse:
        # Build the prompt using the shared builder
        prompt = build_metadata_enrichment_prompt(
            scraped,
            video_filename=video_filename,
            source_url=source_url,
            duration_seconds=duration_seconds,
            channel_name=channel_name,
            upload_date=upload_date,
            mismatch_signals=mismatch_signals,
            fingerprint_hint=fingerprint_hint,
            review_description_only=review_description_only,
            platform_description=platform_description,
            platform_tags=platform_tags,
            custom_enrichment_template=custom_enrichment_template,
            custom_review_template=custom_review_template,
        )

        sys_prompt = custom_system_prompt or SYSTEM_PROMPT

        # Make the API call
        result = self._call_api(
            system_prompt=sys_prompt,
            user_prompt=prompt,
            max_tokens=1200,
        )

        # Parse with retry support
        def retry_fn(repair_prompt: str) -> str:
            retry_result = self._call_api(
                system_prompt=sys_prompt,
                user_prompt=repair_prompt,
                max_tokens=1200,
            )
            return retry_result["content"]

        try:
            parsed, was_retry = parse_enrichment_response(
                result["content"],
                retry_fn=retry_fn,
            )
            if was_retry:
                logger.info("Local LLM response required JSON repair retry")
        except ValueError as e:
            logger.error(f"Local LLM returned unparseable response: {e}")
            return AIMetadataResponse(
                raw_response=result["content"],
                prompt_used=prompt,
                tokens_used=result["tokens"],
                model_name=self.get_model_name(),
            )

        # Extract structured metadata
        meta = extract_ai_metadata(
            parsed,
            tokens=result["tokens"],
            model_name=self.get_model_name(),
            raw_response=result["content"],
        )

        return AIMetadataResponse(
            artist=meta["artist"],
            title=meta["title"],
            album=meta["album"],
            year=meta["year"],
            plot=meta["plot"],
            genres=meta["genres"],
            director=meta.get("director"),
            studio=meta.get("studio"),
            tags=meta.get("tags"),
            field_scores=meta["field_scores"],
            overall_confidence=meta["overall_confidence"],
            identity=meta.get("identity", {}),
            mismatch_info=meta.get("mismatch_info", {}),
            change_summary=meta.get("change_summary", ""),
            raw_response=meta["raw_response"],
            prompt_used=prompt,
            tokens_used=meta["tokens_used"],
            model_name=meta["model_name"],
        )

    def generate_plot(
        self,
        artist: str,
        title: str,
        existing_plot: Optional[str] = None,
        source_url: Optional[str] = None,
        max_length: int = 300,
    ) -> Optional[str]:
        existing_context = ""
        if existing_plot:
            existing_context = f"\nExisting description (may be incomplete): {existing_plot[:200]}"
        if source_url:
            existing_context += f"\nSource: {source_url}"

        prompt = PLOT_GENERATION_PROMPT.format(
            artist=artist,
            title=title,
            existing_context=existing_context,
            max_length=max_length,
        )

        try:
            result = self._call_api(
                system_prompt="You are a concise media library assistant. Write music video descriptions.",
                user_prompt=prompt,
                max_tokens=max_length // 2,
            )
            return result["content"].strip()[:max_length]
        except Exception as e:
            logger.error(f"Local LLM plot generation failed: {e}")

        return None
