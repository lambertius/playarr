# Queue, Database Writes, and Sidecar Concurrency Audit

**Completed:** 2026-08-02
**Scope:** every HTTP mutation surface, background pipeline family, durable actor,
job/status writer, file journal, contribution outbox, sidecar writer, migration,
and raw SQLite connection in `backend/app`.

## Decision

Playarr will retain SQLite for its default single-host deployment, using one
short-transaction writer boundary with parallel read, download, analysis, and
network phases. Authoritative database changes create durable sidecar/file
intent in the same database transaction; reconcilers materialise those intents
after commit.

Sidecars will **not** become an inbound temporary write queue. A watched folder
cannot atomically express a database change plus a delete, rename, relationship
change, revision precondition, or ordered retry. It would replace database lock
contention with lost-update, duplicate-delivery, ordering, and crash-window
problems. Temporary files remain appropriate only inside atomic sidecar/file
replacement, where Playarr already stages, validates, and renames them.

This follows the established constraints and patterns:

- SQLite WAL permits readers and a writer to overlap, but still permits only
  [one writer at a time](https://www.sqlite.org/wal.html#concurrency).
- SQLite explicitly documents that a second write transaction returns
  [`SQLITE_BUSY`](https://www.sqlite.org/lang_transaction.html#read_transactions_versus_write_transactions).
- The transactional outbox pattern records the entity change and downstream
  intent in one transaction, then uses an idempotent consumer
  ([AWS Prescriptive Guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html)).
- Revision predicates detect stale concurrent updates instead of silently
  overwriting them ([SQLAlchemy version counters](https://docs.sqlalchemy.org/en/20/orm/versioning.html)).

## Repository inventory

The audit used syntax/source searches plus execution-path inspection. Counts
are lexical sites, not estimates of runtime frequency.

| Area | Commit sites | Flush sites | HTTP mutation routes |
|---|---:|---:|---:|
| Routers | 167 | 20 | 140 |
| New Videos routers/services | 13 | 4 | 11 |
| Shared services | 32 | 50 | 0 |
| URL pipeline | 23 | 8 | 0 |
| Library pipeline | 6 | 5 | 0 |
| Legacy/common pipeline | 5 | 0 | 0 |
| AI services | 8 | 9 | 0 |
| Metadata services | 1 | 6 | 0 |
| Task orchestration | 43 | 18 | 0 |

Ten raw `sqlite3.connect` sites were inspected. Five are online backup/restore
or read-only job observation connections. The job-status raw write sites are
inside the shared write queue; the terminal emergency fallback now explicitly
acquires the same writer lock. The only direct Playarr XML materialiser is the
sidecar reconciler (the other lexical occurrence is its definition).

## Enforced write model

```text
UI/API
  |-- read ------------------------------------------> WAL snapshot readers
  |-- quick authoritative change --> MutationCommand (idempotent, revisioned)
  |-- start long work -------------> ProcessingJob --> parallel I/O/CPU/network
                                                       |
single writer boundary <-------------------------------+
  |-- short aggregate transaction
  |     |-- database mirror update
  |     |-- sidecar outbox intent
  |     `-- file-operation intent where required
  |
  |-- sidecar reconciler --> atomic .playarr.xml replacement
  `-- file reconciler ----> staged rename/replace + DB path checkpoint
```

### Interactive HTTP mutations

All request dependencies use the guarded `RequestSessionLocal`. Its first SQL
write acquires the process writer boundary and commit/rollback releases it.
Network, yt-dlp, ffmpeg, download, and long analysis work must not occur after
the first write in an HTTP transaction; those endpoints create a job/command
and return.

### Durable mutation and reconciliation actors

The audit found that these actors previously used unguarded `SessionLocal`, so
they could collide with the pipeline write queue despite being single-consumer
internally. They now use `SerializedSessionLocal`, the role-oriented alias for
the guarded engine. Each individual actor transaction is serialised; filesystem
work between file-operation checkpoints does not hold the writer boundary.

### Deployment boundary

The single-process profile requires exactly one web process and may use SQLite.
The Redis profile now refuses SQLite at startup: its web and worker processes
cannot share an in-memory writer guard. Multi-process deployments must use a
server database such as PostgreSQL, whose transaction manager coordinates
writes across processes.

### Pipeline writes

Downloads, ffmpeg, provider calls, scraping, AI, and workspace construction run
in parallel. Aggregate application is short and passes through the durable
mutation actor. Cosmetic progress/log updates use a bounded, coalescing write
queue so progress storms cannot starve authoritative work.

### Lost-update protection

Mutable video, preference, review, consolidation, playlist, and file-operation
aggregates use revisions or stable occurrence identities. A stale UI revision
returns a structured conflict instead of applying an older browser snapshot.
New authoritative aggregates must add a non-null revision and an expected
revision precondition before gaining a mutation endpoint.

### Database/sidecar mirror

The database is the transactional working mirror; `.playarr.xml` plus the
library manifest is the portable authoritative reconstruction format. The
sidecar outbox row is created in the same transaction as the database change.
The reconciler is retryable and idempotent, so a crash can delay a sidecar but
cannot silently lose the intent to produce it.

## UI non-blocking contract

- Long actions return a job or operation identifier after admission.
- UI controls enter a queued/pending state; pages do not navigate merely to
  show that the action was accepted.
- Queue shows authoritative status tabs (`Active`, `Complete`, `Failed`,
  `Cancelled`, `Skipped`) and source subtabs (`All`, `Downloads`, `Imports`,
  `Video Editor`, `Scraper`). Classification lives in one backend registry.
- Queue prominently shows installed/latest yt-dlp versions and an update
  action; the update itself is a visible background job.
- Queue System Health exposes mutation/outbox backlogs, retries, and writer-lock
  wait p99. Lock wait is measured before acquisition, while transaction time is
  measured from the first write through commit/rollback.

### Cross-workflow consistency

Queue retains job-specific progress cards, but follows the same interaction
contract as Review, Video Editor, Metadata, and Archive: one page heading and
action row, persistent top-level navigation, a single subordinate filter row,
standard selected-item checkboxes, explicit pending/empty/error feedback,
confirmation before destructive bulk actions, and toast feedback without
forced navigation. These conventions replace Queue's former duplicate
status/history/type filter rows without hiding controls in a collapsible panel.

## Operational thresholds and escalation

| Signal | Healthy | Investigate | Action |
|---|---:|---:|---|
| Writer wait p99 | <250 ms | 250–2,000 ms | Find transaction doing I/O after first write |
| Writer wait p99 | — | >2,000 ms | Treat as an incident; use slow transaction samples |
| Pending mutations | <25 | 25–250 | Inspect oldest age and failing handler |
| Pending mutations | — | queue limit | Admission returns retryable 429; do not block indefinitely |
| Failed sidecars/file ops | 0 | >0 | Retry/reconcile from Operations health |

SQLite remains the supported default while Playarr is single-host and
write-light. A multi-process or sustained write-heavy deployment should use the
Redis worker profile and a server database such as PostgreSQL; adding more
SQLite writer connections cannot increase write concurrency.

## Regression gates

- concurrent guarded requests plus pipeline writes: no `SQLITE_BUSY`, lost
  writes, deadlocks, or leaked writer lock;
- mutation, sidecar, contribution, and file reconcilers use the guarded actor
  session factory;
- stale aggregate revisions fail rather than overwrite;
- sidecar intent commits atomically with authoritative mutations;
- Queue taxonomy classifies every durable job in one registry;
- production frontend build and full backend suite remain green.
