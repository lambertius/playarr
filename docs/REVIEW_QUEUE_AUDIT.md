# Review Queue Compliance Audit

**Completed:** 2026-08-02
**Scope:** review detection, durable evidence, comparison UI, staged decisions,
file/sidecar consequences, dismissal persistence, and obsolete-case cleanup.

## Outcome

The legacy per-video flag list and the newer evidence-case panel were both
rendered on the Review page. They disagreed about grouping, actions, filters,
and when a decision became permanent. The legacy presentation has been removed.
Review Queue now has one durable case API and one comparison-and-commit UI.

## Supported review classes

| User goal | Structured case categories | Detection/evidence |
|---|---|---|
| Same video at different quality | `duplicate` | title, recording ID, audio fingerprint, perceptual hash, Playarr IDs and quality signature |
| Alternate/live/cover/remix ambiguity | `version_ambiguity`, `version_detection` | version-tolerant title matching and import classification evidence |
| Low-certainty or incomplete import | `low_certainty_import`, `requested_step_incomplete`, `ai_pending`, `ai_partial` | canonical confidence plus requested job inputs compared with completed processing-state steps |
| Normalization problem | `normalization_failure`, `normalization_mismatch` | task failure or completed-normalization loudness more than 0.5 LUFS from the current target |
| Media present without a database owner | `orphan_file`, `scanned` | library-root filesystem scan compared with tracked paths |

Reason text is display evidence only. Classification uses structured categories,
job inputs, processing state, media identity and quality fields; it does not
infer a category by parsing a human-readable error string.

## Pairwise duplicate invariant

A duplicate case contains exactly two candidates and one unordered evidence
edge. Three candidates produce three independently resolvable comparisons:

```text
V1 -- V2
V1 -- V3
V2 -- V3
```

Stable video IDs form the case identity, so rescans update the same case rather
than creating duplicates. Deleting V1 obsoletes both cases containing V1 while
leaving V2--V3 open. Dismissing or resolving one pair stores the mutual
not-duplicate relationship used by the legacy scanner and clears a video's
transition flag only after that video has no open pair remaining.

Evidence changes are hashed. An unchanged dismissal survives rescans; changed
checksums, identities or comparison evidence increment the case revision.
Plans also carry the expected revision, so a stale browser cannot overwrite a
decision made on another device.

## Comparison and decision workflow

- Duplicate candidates receive equal-width A/B panels with one inline video
  player per side. Starting a player replaces the previous active preview.
- Singleton version, enrichment and volume cases use a half-player,
  half-details layout.
- Artist/title, exact added date and relative age, duration, resolution, codecs,
  bitrate, loudness, file size, source and version appear as comparisons.
- The header states why the case was flagged and displays evidence confidence.
- Reclassification uses a bounded native selector that cannot overflow the
  viewport.
- Reclassify, delete, rescrape and normalize choices remain local draft state.
  `Undo changes` clears the draft. `Save changes` previews consequences and
  commits the revision-checked plan. `Dismiss` is a separate no-change outcome.

Reclassification updates metadata, schedules the portable sidecar, and queues
the expected file/sidecar/artwork rename through the durable file journal.
Deletion is recoverable: companions move under the review-delete archive before
the database owner is removed. Repair actions create visible Queue jobs.

## Page consistency

Review now follows the same visual contract as Queue: one heading/action row,
one scan strip, one persistent top-level tab row, one result-control row,
standard loading/error/empty feedback, standard confirmation and toast
feedback, and server-side pagination. The former duplicate filter pills,
parallel evidence panel, bulk per-video actions, hidden fallback layout and
reason-text category inference are no longer exposed.

The selected review group and page size use the server-backed `review`
preference group, so the working layout follows the user across devices.

## Regression gates

- a three-video cluster creates exactly three two-item cases;
- resolving one case leaves the other two open;
- deleting one video obsoletes every affected case but not the remaining pair;
- unchanged dismissal evidence stays dismissed and changed evidence reopens;
- stale revisions return a conflict;
- requested processing and normalization drift produce structured cases;
- reclassification schedules sidecar and durable rename intent;
- production frontend build and full backend/frontend suites remain green.
