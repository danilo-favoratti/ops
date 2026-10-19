# ADR 0010: Keep finding lifecycle transitions append-only

## Status

Accepted.

## Context

Findings retain their identity and original evidence when a later successful
check supersedes them. PDF links and agent records reference that identity with
foreign keys. DuckDB rewrites updates to indexed columns as delete and insert
operations. Both the status index and the implicit foreign-key index on
`superseded_by_run_id` caused such updates to fail for referenced findings.
Removing only the explicit status index was therefore insufficient.

## Decision

Apply the additive `0007a_findings_lifecycle` migration. Store immutable finding
content in `finding_records` and append each supersession to
`finding_supersessions`, with foreign keys to the finding and the check run.
A primary key on the superseded finding permits only one transition, consistent
with ADR 0009's prohibition on reactivating a historical identity.

Expose the existing column order, types, provenance, and current status through
the `findings` view. Rule publication inserts original records and supersession
events in the same transaction; readers continue to use the view.

The migration copies existing findings and supersession history and retargets
PDF/agent foreign keys to the immutable records. It recreates the three dependent
tables transactionally, preserving every column value and ID. Private trace
chunks, invoice text, snapshots, and source records remain untouched. Do not
edit previously committed migrations or disable foreign-key constraints.

## Consequences

Fresh and existing databases share the same migration path. No update or delete
of a referenced finding is needed during normal publication. An interrupted or
failed migration rolls back schema changes, data changes, and migration history
together. Existing external SQL readers retain the `findings` interface; writers
must use the repository and its immutable storage contract.

The lifecycle read adds a join; immutable rule and document indexes remain
available. Upgrade and rollback tests cover historical status, foreign keys,
PDF links, annotations, full traces, and repeated migration application.

Reference: [DuckDB index limitations](https://duckdb.org/docs/current/sql/indexes).
