"""
Theatre backdrop — a pre-rendered, scrolling artwork wall for Kodi's theatre stream.

Kodi can't draw the web app's live artwork wall (it plays video on the native
video plane, behind the GUI), so the theatre experience is delivered as a
server-composited stream: the source video centred over a poster-wall backdrop
that scrolls vertically behind it (mirroring the web wall's motion).

The wall is rendered ONCE to a cached PNG and reused; the per-track ffmpeg job
crops a scrolling window of it and overlays the video — the scroll is a free
side-effect of the encode that already happens. To make the scroll loop
seamlessly, the montage is rendered to a `SCROLL_SPAN`-tall image and then
stacked twice (so a window scrolling 0→SCROLL_SPAN always has identical pixels
SCROLL_SPAN below, and wraps with no seam).

The cache is regenerated only when the library's artwork has grown measurably
(e.g. 100 → 200 posters looks very different), tracked via a sidecar count file.
"""
import logging
import math
import os
import random
import threading

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import MediaAsset

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# Output canvas and the vertical distance the wall scrolls before looping.
CANVAS_W = 1920
CANVAS_H = 1080
SCROLL_SPAN = CANVAS_H * 3  # 3 screens of wall per loop

# Regenerate when posters grow by ≥25% or ≥50 (whichever bites first), so the
# wall stays representative as a library fills out without churning.
_REGEN_GROWTH = 1.25
_REGEN_ABS = 50


def _backdrop_path() -> str:
    return os.path.join(
        get_settings().asset_cache_dir, "theatre", f"wall_{CANVAS_W}x{SCROLL_SPAN}.png"
    )


def _meta_path(out_path: str) -> str:
    return out_path + ".count"


def _read_count(out_path: str) -> int:
    try:
        with open(_meta_path(out_path), "r", encoding="utf-8") as fh:
            return int(fh.read().strip() or "0")
    except (OSError, ValueError):
        return 0


def _write_count(out_path: str, count: int) -> None:
    try:
        with open(_meta_path(out_path), "w", encoding="utf-8") as fh:
            fh.write(str(count))
    except OSError:
        pass


def _poster_paths(db: Session) -> list[str]:
    rows = (
        db.query(MediaAsset.file_path)
        .filter(MediaAsset.asset_type == "poster", MediaAsset.status == "valid")
        .all()
    )
    return [r[0] for r in rows if r[0] and os.path.isfile(r[0])]


def _needs_regen(out_path: str, current: int) -> bool:
    if not os.path.isfile(out_path):
        return True
    prev = _read_count(out_path)
    if prev <= 0:
        return True
    return current >= prev * _REGEN_GROWTH or (current - prev) >= _REGEN_ABS


def _cover_resize(im, tw: int, th: int):
    """Resize + centre-crop so the image fills a tw×th tile (poster ≈ 2:3)."""
    from PIL import Image
    sw, sh = im.size
    scale = max(tw / sw, th / sh)
    nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - tw) // 2, (nh - th) // 2
    return im.crop((left, top, left + tw, top + th))


def _generate(out_path: str, poster_paths: list[str]) -> None:
    from PIL import Image
    cols = max(4, round(CANVAS_W / 240))
    tile_w = math.ceil(CANVAS_W / cols)
    tile_h = round(tile_w * 1.5)  # posters are ~2:3
    rows = math.ceil(SCROLL_SPAN / tile_h)

    montage = Image.new("RGB", (CANVAS_W, SCROLL_SPAN), (10, 12, 18))
    if poster_paths:
        shuffled = list(poster_paths)
        random.shuffle(shuffled)
        idx = 0
        for r in range(rows):
            for c in range(cols):
                path = shuffled[idx % len(shuffled)]
                idx += 1
                try:
                    with Image.open(path) as src:
                        tile = _cover_resize(src.convert("RGB"), tile_w, tile_h)
                except Exception:  # noqa: BLE001 — skip unreadable posters
                    continue
                montage.paste(tile, (c * tile_w, r * tile_h))

    # Darken so the centred video reads clearly on top.
    montage = Image.blend(montage, Image.new("RGB", montage.size, (0, 0, 0)), 0.55)

    # Stack the montage twice so a window scrolling 0→SCROLL_SPAN wraps seamlessly.
    wall = Image.new("RGB", (CANVAS_W, SCROLL_SPAN * 2))
    wall.paste(montage, (0, 0))
    wall.paste(montage, (0, SCROLL_SPAN))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    wall.save(out_path, "PNG")
    logger.info("Theatre wall rendered (%dx%d, %d posters) -> %s",
                CANVAS_W, SCROLL_SPAN * 2, len(poster_paths), out_path)


def ensure_backdrop(db: Session) -> str | None:
    """Return the cached scrolling-wall path, (re)rendering it if missing/stale.

    Returns None only if rendering fails (e.g. Pillow unavailable) so callers can
    fall back to a plain compatibility stream.
    """
    out_path = _backdrop_path()
    try:
        with _lock:
            posters = _poster_paths(db)
            if _needs_regen(out_path, len(posters)):
                _generate(out_path, posters)
                _write_count(out_path, len(posters))
        return out_path if os.path.isfile(out_path) else None
    except Exception:  # noqa: BLE001 — never let backdrop trouble break playback
        logger.exception("Theatre backdrop generation failed")
        return out_path if os.path.isfile(out_path) else None
