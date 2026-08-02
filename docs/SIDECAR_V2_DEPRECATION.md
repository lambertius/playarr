# Sidecar v2 transition and v1 deprecation

Playarr 2.x reads both v1 and v2 `.playarr.xml` sidecars. It writes v2 as the
authoritative format and includes the limited v1 compatibility attributes
needed by the immediately preceding compatible release. Numeric database row
IDs are never treated as portable identity.

The persisted startup migration report records sidecars read by schema,
whether v1 reads remain enabled, the active write schema, and whether v1
compatibility fields were emitted. This makes rollback readiness measurable
rather than inferred.

The planned v1-write exit is Playarr 3.0, after at least one complete 2.x
release has shipped with dual-read coverage. Playarr will retain v1 reads for
the full 3.x line. Removing v1 reads requires a separate migration notice and
an acceptance fixture proving that all discovered sidecars have been rewritten
or explicitly quarantined. Library media is never deleted as part of sidecar
schema migration.
