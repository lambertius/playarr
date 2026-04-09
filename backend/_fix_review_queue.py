"""One-time fix: clear review flags for items that have been AI-enriched
but are still stuck in the review queue (1.9.4 bug)."""
import sqlite3
import json
import sys

DB = r"C:\Users\haydn\AppData\Roaming\Playarr\data\playarr.db"

def main():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    rows = db.execute(
        "SELECT id, artist, title, review_category, review_reason, processing_state "
        "FROM video_items WHERE review_status = 'needs_human_review' "
        "AND review_category IN ('ai_pending', 'ai_partial')"
    ).fetchall()

    print(f"Found {len(rows)} items in ai_pending/ai_partial review queue\n")

    cleared = 0
    for r in rows:
        ps = json.loads(r["processing_state"] or "{}")
        ai_done = ps.get("ai_enriched", {}).get("completed", False)
        scenes_done = ps.get("scenes_analyzed", {}).get("completed", False)
        reason = r["review_reason"] or ""

        need_ai = "AI metadata" in reason
        need_scenes = "scene analysis" in reason

        should_clear = False
        if need_ai and need_scenes:
            should_clear = ai_done  # AI was the action taken; scene analysis is optional
        elif need_ai:
            should_clear = ai_done
        elif need_scenes:
            should_clear = scenes_done
        else:
            should_clear = ai_done  # fallback

        status = f"ai={'Y' if ai_done else 'N'} scenes={'Y' if scenes_done else 'N'}"
        if should_clear:
            print(f"  CLEAR #{r['id']:4d} {r['artist']} - {r['title']}  ({status})")
            cleared += 1
        else:
            print(f"  KEEP  #{r['id']:4d} {r['artist']} - {r['title']}  ({status})")

    if cleared == 0:
        print("\nNo items to clear.")
        db.close()
        return

    print(f"\n{cleared} items will be cleared. Proceed? [y/N] ", end="")
    if input().strip().lower() != "y":
        print("Aborted.")
        db.close()
        return

    # Apply the fix
    for r in rows:
        ps = json.loads(r["processing_state"] or "{}")
        ai_done = ps.get("ai_enriched", {}).get("completed", False)
        reason = r["review_reason"] or ""
        need_ai = "AI metadata" in reason
        need_scenes = "scene analysis" in reason

        should_clear = False
        if need_ai and need_scenes:
            should_clear = ai_done
        elif need_ai:
            should_clear = ai_done
        elif need_scenes:
            should_clear = ps.get("scenes_analyzed", {}).get("completed", False)
        else:
            should_clear = ai_done

        if should_clear:
            db.execute(
                "UPDATE video_items SET review_status = 'none', "
                "review_reason = NULL, review_category = NULL "
                "WHERE id = ?", (r["id"],)
            )

    db.commit()
    print(f"\nDone. Cleared {cleared} items from review queue.")
    db.close()


if __name__ == "__main__":
    main()
