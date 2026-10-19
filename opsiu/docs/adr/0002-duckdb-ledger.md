# ADR 0002: DuckDB storage and a shared run ledger

Status: Accepted.

## Context

Audits need analytical queries over reimbursement records and a durable account
of each operation. Failures must remain inspectable even during initial database
creation.

## Decision

Use an embedded DuckDB database with plain SQL migrations applied in lexical
order. Apply each migration in its own transaction and record its identifier and
timestamp. Never edit a committed migration.

Every command and web action uses the application run lifecycle with a UUIDv7
identifier, steps, events, and a terminal status. Persist the ledger in `runs`,
`steps`, and `events`; append the same events to a unique run directory as JSONL.
Record normalized failures and finish ordinary step failures as
`completed_with_errors`. Write a manifest when the run finishes. Step 6.2 adds
the Portuguese `report.md` rendered from public ledger records after the manifest;
it does not read private agent content.

Start durable file logging before opening the database. During initial migration,
buffer the current run's database records and replay them transactionally when
the ledger schema exists. Commands before migration can retain file-only runs;
history inspection includes those records after migration.

## Consequences

Local operation needs no database server, and SQL can join audit results to their
execution history. File records preserve bootstrap failures and support inspection
without a migrated database. The database and file sinks are separate durability
boundaries, so their event identifiers must reconcile. Schema changes require a
new migration and a changelog entry.

Source: [run lifecycle](../../ARCHITECTURE.md#application-run-lifecycle).
