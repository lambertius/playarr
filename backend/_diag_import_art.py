import sqlite3, json, os

c = sqlite3.connect('playarr.db', timeout=10)

# Find library import video children directly
rows = c.execute(
    "SELECT id, video_id, status FROM processing_jobs WHERE job_type='library_import_video'"
).fetchall()
print(f"Total library_import_video jobs: {len(rows)}")
statuses = {}
video_ids = []
for jid, vid, st in rows:
    statuses[st] = statuses.get(st, 0) + 1
    if vid:
        video_ids.append(vid)
for st, cnt in sorted(statuses.items(), key=lambda x: -x[1]):
    print(f"  {st}: {cnt}")
print(f"Jobs with video_id: {len(video_ids)}")

# Check media_assets for imported videos
no_poster = 0
no_thumb = 0
no_assets = 0
has_poster_and_thumb = 0
poster_missing_disk = 0
thumb_missing_disk = 0
sample_issues = []

for vid in video_ids:
    # Get video info
    vrow = c.execute(
        'SELECT id, artist, title, review_status, review_category FROM video_items WHERE id=?', (vid,)
    ).fetchone()
    if not vrow:
        continue

    # Check media_assets
    assets = c.execute(
        "SELECT asset_type, file_path, status FROM media_assets WHERE video_id=?", (vid,)
    ).fetchall()
    posters = [a for a in assets if a[0] == 'poster']
    thumbs = [a for a in assets if a[0] == 'thumbnail']

    # Check ai_thumbnails
    ai_thumbs = c.execute(
        "SELECT id, file_path, is_selected FROM ai_thumbnails WHERE video_id=?", (vid,)
    ).fetchall()

    has_poster = len(posters) > 0
    has_thumb = len(thumbs) > 0 or len(ai_thumbs) > 0

    # Check disk existence
    poster_on_disk = any(os.path.isfile(p[1]) for p in posters if p[1]) if posters else False
    thumb_on_disk = (
        any(os.path.isfile(t[1]) for t in thumbs if t[1]) or
        any(os.path.isfile(t[1]) for t in ai_thumbs if t[1])
    ) if (thumbs or ai_thumbs) else False

    if has_poster and not poster_on_disk:
        poster_missing_disk += 1
    if has_thumb and not thumb_on_disk:
        thumb_missing_disk += 1

    if not has_poster and not has_thumb:
        no_assets += 1
    elif not has_poster:
        no_poster += 1
    elif not has_thumb:
        no_thumb += 1
    else:
        has_poster_and_thumb += 1

    if (not has_poster or not poster_on_disk or not has_thumb or not thumb_on_disk) and len(sample_issues) < 8:
        sample_issues.append(
            f"  v{vrow[0]} {vrow[1]} - {vrow[2]}: "
            f"posters={len(posters)}(disk={poster_on_disk}) "
            f"thumbs={len(thumbs)} ai_thumbs={len(ai_thumbs)}(disk={thumb_on_disk}) "
            f"review={vrow[3]}/{vrow[4]} "
            f"asset_statuses={[a[2] for a in assets]}"
        )

print(f"\nArtwork state of {len(video_ids)} imported videos:")
print(f"  Has poster+thumb: {has_poster_and_thumb}")
print(f"  Missing poster only: {no_poster}")
print(f"  Missing thumb only: {no_thumb}")
print(f"  Missing both: {no_assets}")
print(f"  Poster in DB but missing on disk: {poster_missing_disk}")
print(f"  Thumb in DB but missing on disk: {thumb_missing_disk}")

if sample_issues:
    print(f"\nSample issues ({len(sample_issues)}):")
    for s in sample_issues:
        print(s)

# Check review_status
review_set = 0
review_cats = {}
for vid in video_ids:
    row = c.execute('SELECT review_status, review_category FROM video_items WHERE id=?', (vid,)).fetchone()
    if row and row[0]:
        review_set += 1
        cat = row[1] or 'none'
        review_cats[cat] = review_cats.get(cat, 0) + 1
print(f"\nWith review_status set: {review_set}")
for cat, cnt in sorted(review_cats.items(), key=lambda x: -x[1]):
    print(f"  {cat}: {cnt}")

c.close()
print(f"\nWith review_status set: {review_set}")
for cat, cnt in sorted(review_cats.items(), key=lambda x: -x[1]):
    print(f"  {cat}: {cnt}")

c.close()
