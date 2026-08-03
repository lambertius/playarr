# Sidecar reconstruction contract

Playarr sidecars are the portable authority for a library. SQLite remains the
transactional runtime store, but deleting it and rebuilding from a moved media
tree, its `.playarr.xml` files, the library manifest, and archive manifests must
recreate the same logical library without relying on old numeric row IDs or old
absolute paths.

## Portable-authoritative state

Each video sidecar carries the immutable entity UUID, deterministic video and
track IDs, entity/sidecar revisions, media signature, relative file and artwork
paths, metadata, sources, quality analysis, ratings, version relationships,
review flags, processing state, and complete provenance events.

The versioned `portable_state` section additionally preserves:

- AI metadata runs, including provider/model, requested and accepted fields,
  status, confidence, prompt/response, token use, errors, and timestamps;
- scene analyses, scene boundaries, configuration, failures, and selected
  thumbnail candidates;
- per-field user attribution, verification, rating attribution, and original
  entity timestamps;
- metadata snapshots, normalization history, contribution history, matching
  results, pinned decisions, and video-editor queue drafts.

The hashed `.playarr-library-manifest.json` carries aggregates that cannot
belong to one video: playlists and occurrence IDs, artist/genre
consolidations, review cases and action plans, and historical archive/restore
operations. Its `.bak` is an automatic recovery source if the primary is
missing, corrupt, or fails hash validation.

Every archive manifest carries `entity_id`, `playarr_video_id`, checksum,
original relative path, operation ID, reason, and timestamps. Archive scans
resolve the immutable entity UUID first and use the content ID/checksum as
corroborating identity, so moving the archive root does not break linkage.

## Portable-derived state

Numeric primary keys, absolute paths, normalized genre rows, association-table
row IDs, entity-graph row IDs, thumbnails that can be regenerated, search
indexes, and archive-catalog rows are recreated from the authoritative values.
They are not compared by their old SQLite IDs.

## Instance-only state

Application settings, device-independent UI preferences, credentials, tool
paths, and deployment configuration remain in the instance database. UI
preferences are server-backed and revisioned so browsers on that instance
converge, but they are deliberately not library metadata.

## Ephemeral state

Completed diagnostic job logs, active HTTP sessions, caches, worker leases,
and already-consumed outbox commands are not library state. Durable unfinished
filesystem operations and sidecar/contribution outboxes remain recoverable in
the live instance; completed archive/restore provenance is projected into the
portable manifests.

## Import acceptance rules

1. Validate XML structure and recompute `contentHash` before any mutation.
2. Resolve adjacent/stored media, then checksum-search the selected root; size
   and signature must match uniquely.
3. Restore entities by UUID/PVD in pass one and relationships in pass two.
4. Validate the global manifest, falling back to `.bak`; report an incomplete
   rebuild if neither valid copy exists.
5. Quarantine rejected or ambiguous inputs without committing partial rows.
6. Rebuild archive catalog links from portable archive manifests after either
   root moves.

The acceptance tests in `backend/tests/test_sidecar_rebuild.py` compare logical
state across databases with different numeric IDs and explicitly cover AI,
scene, attribution, history, editor-draft, moved-file, and media-tamper cases.
