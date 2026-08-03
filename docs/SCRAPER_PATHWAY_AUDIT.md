# Scraper and import pathway audit

Last reviewed: 2026-08-02

## Contract

Every metadata operation is represented by an immutable `ImportPolicy` and
`ImportContext`. The production and diagnostic entry points call
`pipeline.import_context.run_metadata_stage`; no task or router calls the
scraper resolver directly.

The supported modes are:

| Mode | External providers | AI source resolution | AI final review |
|---|---|---:|---:|
| Existing only | None | No | No |
| Wiki only | Wikipedia | No | No |
| MusicBrainz only | MusicBrainz and its Cover Art Archive | No | No |
| Scrapers | Wikipedia and MusicBrainz | No | No |
| AI Auto | Wikipedia, MusicBrainz and IMDB discovery | Yes | Yes, after scraper validation |
| AI Only | None | Yes | Yes |

`ai_auto_fallback` remains accepted as a legacy API field, but it is normalized
to `ai_only`. It must never enable Wikipedia or MusicBrainz. New internal code
must use the typed policy role instead of interpreting this transport name.

## Entry-point matrix

| User pathway | Boundary | Production metadata stage |
|---|---|---|
| Add video URL | jobs router -> URL pipeline | Shared context, `url_add` |
| Playlist | jobs router -> child URL pipelines | Shared context per child, `url_add` |
| New Videos/cart | new-videos router -> URL pipeline | Shared context, `url_add` |
| Import from disk | library-import router -> disk pipeline | Shared context, `disk_import` |
| Metadata rescan | rescan task | Shared context, `rescan` |
| AI Auto/AI Only action | metadata task | Shared context, `metadata_action` |
| MusicBrainz action | metadata task | Shared context, `metadata_action` |
| Scraper Tester URL | scraper-test router | Shared context, `scraper_test`, dry run |
| Scraper Tester file | scraper-test router | Shared context, `scraper_test`, dry run |

The tester can perform source-scoped artist/album artwork diagnostics after the
metadata stage. Those helpers are gated by the same effective policy:

- Wiki only cannot call MusicBrainz or Cover Art Archive.
- MusicBrainz only cannot call Wikipedia.
- AI only cannot call any external metadata/artwork scraper.
- AI Auto can call both provider families.

## Direct source links

Direct links are validated before network access. Wikipedia targets must be an
HTTP(S) `*.wikipedia.org/wiki/...` page. MusicBrainz targets must be a
`musicbrainz.org/recording/...` or `musicbrainz.org/release-group/...` page.
A valid direct link implicitly enables that provider even if an older client
omits its checkbox. The resolver passes the link only to the matching provider;
it is recorded as `target: direct_url` in the structured trace.

## Trace and troubleshooting contract

Each diagnostic run persists redacted events for:

1. effective import policy and input identity;
2. AI source-resolution request and response (or an explicit skipped event);
3. MusicBrainz request, response fields, source and decisions;
4. Wikipedia request, response fields, source and decisions;
5. validation failures and rejected/discarded values;
6. AI final-review input and response;
7. final resolved values and provenance sources.

The Scraper Tester renders the effective plan before execution and exposes the
sent/received snapshots for every trace step. Diagnostic bundles are stored as
`JobEvent` rows and redact secrets and local paths before persistence.

## Regression gates

`test_import_policy_and_trace.py`, `test_import_pathway_matrix.py`, and
`test_pipeline_convergence.py` cover legacy-option translation, direct-source
routing, deterministic stage selection, production/dry-run equivalence,
structured trace persistence and redaction.
