# New Videos — Implementation Note

## Display Strategy

Suggestions are rendered as **thumbnail cards** (280 px wide) in responsive grids, one section per category.  Category order: `taste`, `by_artist`, `famous`, `popular`, `new`, `rising` — personalized sections first.  Up to 20 cards per category.
No embedded players — clicking "Open Source" opens the original provider page in a new tab.  
Each card shows: thumbnail, duration overlay, title, artist, channel, trust badge (green/yellow/red), recommendation reason, and action buttons (add/cart/dismiss).

## Recommendation Strategy

The engine is **modular by category**. Each category has its own candidate generator:

| Category | Source |
|----------|--------|
| `famous` | Curated seed list of ~97 iconic music videos (YouTube IDs) + yt-dlp search |
| `popular` | Curated seed list of ~60 popular music videos + yt-dlp search |
| `by_artist` | Artists in the user's library with ≥ `nv_min_owned_for_artist_rec` owned videos (default 1) — seed matching + yt-dlp search for up to 8 artists |
| `taste` | Multi-signal preference scoring: 5-star artists (weight 1.0), 4-star (0.6), 3-star (0.3 if <8 artists), PlaybackHistory play-count bonus (0.05/play, capped 0.5), genre affinity (top 3 genres via yt-dlp) — up to 6 artist searches |
| `new` | yt-dlp search for recent official music video releases |
| `rising` | yt-dlp search for trending/viral music videos |

Generators produce `RecommendationCandidate` objects. The `RecommendationRanker` applies a weighted linear combination:

- **Trust**: 0.40 (quality signal)
- **Popularity**: 0.25 (view count normalised)
- **Trend**: 0.15 (freshness/momentum)
- **Feedback**: 0.10 (user preference adjustment)
- **Freshness**: 0.10 (recency of the video itself)

Category-specific weight overrides exist (e.g. `famous` flattens feedback weight; `by_artist` boosts it).

Results are deduplicated against the user's existing library (by `provider_video_id`), cached in `RecommendationSnapshot`, and filtered by dismissals before being served.

## Trust Scoring

`score_trust()` evaluates source credibility on a 0.0–1.0 scale:

- **VEVO channels** → 0.95 base
- **Official artist channel** (name overlap check) → 0.88 base
- **Negative content patterns** (lyric video, cover, remix, reaction, fan-made, unofficial, bootleg, compilation) → penalties of 0.10–0.20
- **Hard-blocked content** — multi-hour compilations ("N hours of"), full albums, nonstop mixes, megamixes → score 0.0
- **View count signal** — ≥100M views → +0.05 bonus; ≥10M → +0.03; <10K → −0.05
- **Duration sanity** — <60s → −0.15; >8 min → −0.25; >15 min → hard-blocked (score 0.0)

Returns a `TrustResult` with the numeric score, list of reasons, list of penalties, and inferred `source_type` (`vevo` / `official` / `label` / `user_upload` / `unknown`).

## Learning & Feedback Foundation

Every meaningful user action records a `RecommendationFeedback` event:

| Action | `feedback_type` |
|--------|-----------------|
| Open source link | `view` |
| Quick import (Add) | `add` |
| Add to cart | `cart_add` |
| Dismiss (temporary) | `dismiss_temporary` |
| Dismiss (permanent) | `dismiss_permanent` |

The `FeedbackAdjuster` queries aggregate feedback to compute per-artist and per-category adjustment scores (−0.3 to +0.3), plus a trusted-channels list (channels with ≥2 imports get a boost).

This creates a concrete learning loop: the ranker's feedback weight uses real interaction data, so recommendations improve as the user engages with the feed. The schema is designed so a future ML model can train directly on the `recommendation_feedback` table.

## Settings

21 configurable keys in a dedicated "Discovery" tab under Settings, organised into six sections: Feed Behaviour, Recommendation Behaviour, Artist Recommendations, Preference-Based, Cart Behaviour, and Category Sizes. Settings are stored in the existing `app_settings` KV table and served by dedicated `/api/new-videos/settings` endpoints.
