import sqlite3

conn = sqlite3.connect("C:/Users/haydn/AppData/Roaming/Playarr/data/playarr.db")
c = conn.cursor()

# List tables
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("Tables:", [r[0] for r in c.fetchall()])

# Check Florence tracks
print("=== Florence tracks ===")
c.execute("SELECT id, artist, title, folder_path, file_path FROM video_items WHERE artist LIKE '%Florence%'")
for r in c.fetchall():
    print(f"ID={r[0]} | {r[1]} - {r[2]}")
    print(f"  folder: {r[3]}")
    print(f"  file:   {r[4]}")
    print()

# Check American Football tracks
print("=== American Football tracks ===")
c.execute("SELECT id, artist, title, folder_path, file_path, song_rating, song_rating_set, video_rating, video_rating_set FROM video_items WHERE artist LIKE '%American Football%'")
for r in c.fetchall():
    print(f"ID={r[0]} | {r[1]} - {r[2]}")
    print(f"  folder: {r[3]}")
    print(f"  song_rating={r[5]} (set={r[6]}) video_rating={r[7]} (set={r[8]})")
    print()

# Check for duplicates by file_path
print("=== Duplicate file_paths ===")
c.execute("SELECT file_path, COUNT(*) as cnt FROM video_items GROUP BY file_path HAVING cnt > 1")
for r in c.fetchall():
    print(f"  {r[1]}x: {r[0]}")

# Check for duplicates by folder_path
print("\n=== Duplicate folder_paths ===")
c.execute("SELECT folder_path, COUNT(*) as cnt FROM video_items GROUP BY folder_path HAVING cnt > 1")
for r in c.fetchall():
    print(f"  {r[1]}x: {r[0]}")

conn.close()
