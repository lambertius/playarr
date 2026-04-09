"""
One-time repair: clear review queue items whose required flags are now satisfied.

Applies the same auto-clear logic introduced in v1.9.5:
- ai_partial/ai_pending: parse review_reason for what's missing, check those flags
- scanned: check metadata_scraped or metadata_resolved
- normalization: check audio_normalized
"""
import sqlite3
import json
import sys
from datetime import datetime

DB_PATH = "playarr.db"

def flag_ok(ps: dict, key: str) -> bool:
    val = ps.get(key)
    if isinstance(val, dict):
        return val.get("completed", False)
    return bool(val)


def should_clear(row) -> tuple[bool, str]:
    cat = row["review_category"] or ""
    reason = row["review_reason"] or ""
    ps = json.loads(row["processing_state"] or "{}")

    if cat in ("ai_partial", "ai_pending"):
        need_ai = "AI metadata" in reason
        need_scenes = "scene analysis" in reason
        if not (need_ai or need_scenes):
            # Fallback: if reason doesn't specify, require ai_enriched
            cleared = flag_ok(ps, "ai_enriched")
            return cleared, f"fallback ai_enriched={cleared}"
        cleared = (not need_ai or flag_ok(ps, "ai_enriched")) and \
                  (not need_scenes or flag_ok(ps, "scenes_analyzed"))
        return cleared, f"need_ai={need_ai}(ok={flag_ok(ps, 'ai_enriched')}) need_scenes={need_scenes}(ok={flag_ok(ps, 'scenes_analyzed')})"

    elif cat == "normalization":
        cleared = flag_ok(ps, "audio_normalized")
        return cleared, f"audio_normalized={cleared}"

    elif cat == "scanned":
        cleared = flag_ok(ps, "metadata_scraped") or flag_ok(ps, "metadata_resolved")
        return cleared, f"scraped={flag_ok(ps, 'metadata_scraped')} resolved={flag_ok(ps, 'metadata_resolved')}"

    return False, f"unknown category: {cat}"


def main():
    dry_run = "--dry-run" in sys.argv

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    rows = cur.execute("""
        SELECT id, title, review_status, review_reason, review_category, processing_state
        FROM video_items
        WHERE review_status NOT IN ('none', 'reviewed')
        ORDER BY review_category, id
    """).fetchall()

    print(f"Total items in review queue: {len(rows)}")
    cats = {}
    for r in rows:
        c = r["review_category"] or "null"
        cats[c] = cats.get(c, 0) + 1
    print(f"By category: {cats}")
    print()

    to_clear = []
    to_keep = []
    for r in rows:
        cleared, detail = should_clear(r)
        if cleared:
            to_clear.append((r["id"], r["title"], r["review_category"], detail))
        else:
            to_keep.append((r["id"], r["title"], r["review_category"], detail))

    print(f"=== WILL CLEAR: {len(to_clear)} items ===")
    for vid, title, cat, detail in to_clear[:20]:
        print(f"  id={vid:4d} [{cat}] {title[:50]} -- {detail}")
    if len(to_clear) > 20:
        print(f"  ... and {len(to_clear) - 20} more")

    print(f"\n=== WILL KEEP: {len(to_keep)} items ===")
    for vid, title, cat, detail in to_keep[:20]:
        print(f"  id={vid:4d} [{cat}] {title[:50]} -- {detail}")
    if len(to_keep) > 20:
        print(f"  ... and {len(to_keep) - 20} more")

    if dry_run:
        print("\n[DRY RUN] No changes made.")
        conn.close()
        return

    if not to_clear:
        print("\nNothing to clear.")
        conn.close()
        return

    ids = [item[0] for item in to_clear]
    placeholders = ",".join("?" * len(ids))
    cur.execute(f"""
        UPDATE video_items
        SET review_status = 'none',
            review_reason = NULL,
            review_category = NULL
        WHERE id IN ({placeholders})
    """, ids)
    conn.commit()
    print(f"\n[DONE] Cleared {cur.rowcount} items from review queue.")
    conn.close()


if __name__ == "__main__":
    main()
