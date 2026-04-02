"""
OpenAI Provider — GPT-4o / GPT-4o-mini for metadata enrichment.
"""
import json
import logging
import random
import time
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

DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIProvider(AIProviderBase):
    """OpenAI ChatGPT provider for AI metadata enrichment."""

    name = "openai"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        self.api_key = api_key
        self.model = model

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def get_model_name(self) -> str:
        return self.model

    # Models that require max_completion_tokens instead of max_tokens
    _NEW_PARAM_MODELS = {"gpt-5", "gpt-5-mini", "gpt-5-nano", "o3-mini", "o4-mini", "o1", "o1-mini"}

    _MAX_RETRIES = 5  # retries on 429 rate-limit errors

    def _call_api(self, system_prompt: str, user_prompt: str, max_tokens: int = 800) -> dict:
        """Make a ChatCompletion API call and return parsed response."""
        # Newer models (GPT-5, o-series) use max_completion_tokens
        use_new_param = any(self.model.startswith(p) for p in self._NEW_PARAM_MODELS)

        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        if use_new_param:
            # Reasoning models (GPT-5, o-series) use internal "thinking" tokens
            # that count against max_completion_tokens.  The caller's max_tokens
            # represents desired *output* size; we add a generous budget for the
            # model's chain-of-thought so the response isn't truncated.
            body["max_completion_tokens"] = max(max_tokens * 10, 16384)
            # GPT-5 / o-series only support temperature=1 (the default)
        else:
            body["max_tokens"] = max_tokens
            body["temperature"] = 0.3

        last_error = None
        for attempt in range(self._MAX_RETRIES + 1):
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=180,
            )

            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                tokens = data.get("usage", {}).get("total_tokens", 0)
                return {"content": content, "tokens": tokens}

            # Parse error detail
            detail = ""
            try:
                err_body = resp.json()
                err_obj = err_body.get("error", {})
                detail = err_obj.get("message", "") or err_obj.get("code", "")
            except Exception:
                detail = resp.text[:200]

            # 429 rate-limit: retry with exponential backoff
            if resp.status_code == 429:
                if "insufficient_quota" in (detail or "").lower():
                    raise RuntimeError("OpenAI: Insufficient quota — add credits at platform.openai.com/account/billing")
                if attempt < self._MAX_RETRIES:
                    # Use Retry-After header if provided, otherwise exponential backoff
                    retry_after = resp.headers.get("retry-after")
                    if retry_after:
                        try:
                            delay = float(retry_after)
                        except ValueError:
                            delay = 2 ** attempt + random.uniform(0, 2)
                    else:
                        delay = 2 ** attempt + random.uniform(0, 2)
                    delay = min(delay, 60)
                    logger.warning(
                        f"OpenAI 429 rate limited (attempt {attempt + 1}/{self._MAX_RETRIES + 1}), "
                        f"retrying in {delay:.1f}s"
                    )
                    time.sleep(delay)
                    continue
                last_error = f"OpenAI: Rate limited — {detail or 'try again in a moment'}"
                break

            logger.error(f"OpenAI API {resp.status_code}: {detail or resp.text[:300]}")

            if resp.status_code == 401:
                raise RuntimeError("OpenAI: Invalid API key")
            elif resp.status_code == 404:
                raise RuntimeError(f"OpenAI: Model '{self.model}' not found — check model name or account access")
            else:
                raise RuntimeError(f"OpenAI API error {resp.status_code}: {detail or 'unknown error'}")

        raise RuntimeError(last_error or "OpenAI API error after retries")

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
                logger.info("OpenAI response required JSON repair retry")
        except ValueError as e:
            logger.error(f"OpenAI returned unparseable response: {e}")
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
            result = self._call_api(
                system_prompt="You are a concise media library assistant. Write short, engaging descriptions for music videos suitable for Kodi.",
                user_prompt=prompt,
                max_tokens=max_length // 2,
            )
            parsed = json.loads(result["content"])
            return parsed.get("plot") or parsed.get("description") or result["content"].strip()
        except (json.JSONDecodeError, KeyError):
            # Fallback: use raw content
            try:
                return result["content"].strip()[:max_length]
            except Exception:
                pass
        except Exception as e:
            logger.error(f"OpenAI plot generation failed: {e}")

        return None

    def rank_thumbnails(
        self,
        image_paths: List[str],
        artist: str = "",
        title: str = "",
    ) -> List[ThumbnailRanking]:
        """Rank thumbnail candidates using OpenAI vision API."""
        if not image_paths:
            return []

        # Build multimodal content: text prompt + base64 images
        prompt_text = THUMBNAIL_RANKING_PROMPT.format(
            artist=artist or "Unknown",
            title=title or "Unknown",
        )

        content: List[dict] = [{"type": "text", "text": prompt_text}]
        for i, img_path in enumerate(image_paths):
            try:
                with open(img_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
                content.append({
                    "type": "text",
                    "text": f"Image {i}:",
                })
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                })
            except OSError:
                logger.warning(f"Could not read thumbnail: {img_path}")
                continue

        # Newer models use max_completion_tokens
        use_new_param = any(self.model.startswith(p) for p in self._NEW_PARAM_MODELS)
        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a thumbnail quality evaluator. Respond only with valid JSON."},
                {"role": "user", "content": content},
            ],
            "response_format": {"type": "json_object"},
        }
        if use_new_param:
            body["max_completion_tokens"] = 4096
        else:
            body["max_tokens"] = 1200
            body["temperature"] = 0.3

        try:
            last_error = None
            for attempt in range(self._MAX_RETRIES + 1):
                resp = httpx.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=180,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    raw = data["choices"][0]["message"]["content"]
                    return self._parse_rankings(raw)

                if resp.status_code == 429 and attempt < self._MAX_RETRIES:
                    delay = min(2 ** attempt + random.uniform(0, 2), 60)
                    logger.warning(f"OpenAI vision 429, retry {attempt + 1} in {delay:.1f}s")
                    time.sleep(delay)
                    continue

                last_error = f"OpenAI vision API {resp.status_code}"
                break

            logger.error(f"OpenAI rank_thumbnails failed: {last_error}")
        except Exception as e:
            logger.error(f"OpenAI rank_thumbnails error: {e}")

        return []

    @staticmethod
    def _parse_rankings(raw: str) -> List[ThumbnailRanking]:
        """Parse JSON rankings response into ThumbnailRanking list."""
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
