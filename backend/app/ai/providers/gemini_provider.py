"""
Gemini Provider — Google Gemini for metadata enrichment.
"""
import json
import logging
from typing import Any, Dict, List, Optional
import base64

import httpx

from app.ai.providers.base import (
    AIProviderBase, AIMetadataResponse, ThumbnailRanking,
    PLOT_GENERATION_PROMPT, THUMBNAIL_RANKING_PROMPT,
)
from app.ai.prompt_builder import build_metadata_enrichment_prompt, SYSTEM_PROMPT
from app.ai.response_parser import parse_enrichment_response, extract_ai_metadata

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.0-flash"
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider(AIProviderBase):
    """Google Gemini provider for AI metadata enrichment."""

    name = "gemini"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        self.api_key = api_key
        self.model = model

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def get_model_name(self) -> str:
        return self.model

    def _call_api(self, prompt: str, max_tokens: int = 800) -> dict:
        """Make a Gemini generateContent call."""
        url = f"{API_BASE}/{self.model}:generateContent?key={self.api_key}"

        resp = httpx.post(
            url,
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "temperature": 0.3,
                    "responseMimeType": "application/json",
                },
            },
            timeout=60,
        )

        if resp.status_code != 200:
            logger.error(f"Gemini API {resp.status_code}: {resp.text[:300]}")
            raise RuntimeError(f"Gemini API error {resp.status_code}")

        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise RuntimeError("Gemini returned no candidates")

        parts = candidates[0].get("content", {}).get("parts", [])
        content = parts[0].get("text", "") if parts else ""

        # Gemini doesn't report tokens the same way; estimate
        tokens = data.get("usageMetadata", {}).get("totalTokenCount", 0)

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

        # Gemini uses a single prompt (system + user combined)
        full_prompt = f"{sys_prompt}\n\n{prompt}"
        result = self._call_api(full_prompt, max_tokens=1200)

        # Parse with retry support
        def retry_fn(repair_prompt: str) -> str:
            retry_result = self._call_api(repair_prompt, max_tokens=1200)
            return retry_result["content"]

        try:
            parsed, was_retry = parse_enrichment_response(
                result["content"],
                retry_fn=retry_fn,
            )
            if was_retry:
                logger.info("Gemini response required JSON repair retry")
        except ValueError as e:
            logger.error(f"Gemini returned unparseable response: {e}")
            return AIMetadataResponse(
                raw_response=result["content"],
                prompt_used=prompt,
                tokens_used=result["tokens"],
                model_name=self.model,
            )

        # Extract structured metadata
        meta = extract_ai_metadata(
            parsed,
            tokens=result["tokens"],
            model_name=self.model,
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
            result = self._call_api(prompt, max_tokens=max_length // 2)
            # Try JSON parse first
            try:
                parsed = json.loads(result["content"])
                return parsed.get("plot") or parsed.get("description") or result["content"].strip()
            except json.JSONDecodeError:
                return result["content"].strip()[:max_length]
        except Exception as e:
            logger.error(f"Gemini plot generation failed: {e}")

        return None

    def rank_thumbnails(
        self,
        image_paths: List[str],
        artist: str = "",
        title: str = "",
    ) -> List[ThumbnailRanking]:
        """Rank thumbnail candidates using Gemini vision API."""
        if not image_paths:
            return []

        prompt_text = THUMBNAIL_RANKING_PROMPT.format(
            artist=artist or "Unknown",
            title=title or "Unknown",
        )

        # Build multimodal parts: text + inline images
        parts: List[dict] = [{"text": prompt_text}]
        for i, img_path in enumerate(image_paths):
            try:
                with open(img_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
                parts.append({"text": f"Image {i}:"})
                parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
            except OSError:
                logger.warning(f"Could not read thumbnail: {img_path}")
                continue

        url = f"{API_BASE}/{self.model}:generateContent?key={self.api_key}"
        try:
            resp = httpx.post(
                url,
                json={
                    "contents": [{"parts": parts}],
                    "generationConfig": {
                        "maxOutputTokens": 1200,
                        "temperature": 0.3,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=180,
            )
            if resp.status_code != 200:
                logger.error(f"Gemini vision API {resp.status_code}: {resp.text[:300]}")
                return []

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return []

            raw = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            parsed = json.loads(raw)
            items = parsed.get("rankings", [])
            rankings = []
            for item in items:
                rankings.append(ThumbnailRanking(
                    index=int(item.get("index", 0)),
                    score=float(item.get("score", 0.5)),
                    has_artist=bool(item.get("has_artist", False)),
                    has_text=bool(item.get("has_text", False)),
                    is_blur=bool(item.get("is_blur", False)),
                    description=str(item.get("description", "")),
                ))
            rankings.sort(key=lambda r: r.score, reverse=True)
            return rankings

        except Exception as e:
            logger.error(f"Gemini rank_thumbnails error: {e}")
            return []
