# ADR 0006: Typer as the command interface

Status: Accepted.

## Context

The audit is operated manually in explicit stages. Operators need discoverable
commands, consistent error handling, and a direct path from terminal output to
the execution record.

## Decision

Use Typer for `python3 -m opsiu` and the installed `opsiu` entry point. Keep command
handlers thin: compose dependencies, invoke application use cases, and render
Portuguese output. The planned command families are `db`, `runs`, `ingest`,
`check`, `fetch`, `explain`, `review`, and `report`.

Every command invocation enters the shared run lifecycle, prints its run identifier
first, and prints its report path last. Inspection commands also produce runs.
Use exit code 0 for success, 1 for operational failures, and 2 for configuration
errors. Provide `runs list` and `runs show <id> [--events]` for history inspection.

## Consequences

Operators and scripts share one interface, and CLI integration tests can verify
commands with temporary settings and storage. Application behavior remains reusable
outside Typer. Commands are added in implementation-plan order; the full report
renderer is scheduled in Step 6.2, so the printed report destination precedes
report-file generation during the initial implementation.

Source: [CLI interface](../../ARCHITECTURE.md#cli-typer).
