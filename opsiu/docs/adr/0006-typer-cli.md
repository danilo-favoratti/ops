# ADR 0006: Typer as the command interface

Status: Accepted.

## Context

The audit is operated manually in explicit stages. Operators need discoverable
commands, consistent error handling, and a direct path from terminal output to
the execution record.

## Decision

Use Typer for `python3 -m opsiu` and the installed `opsiu` entry point. Keep command
handlers thin: compose dependencies, invoke application use cases, and render
Portuguese output. The implemented command families are `db`, `runs`, `ingest`,
`check`, `fetch`, `explain`, `review`, and `report`.

Every dispatched command enters the shared run lifecycle, prints its run identifier
first, and prints its report path last. Inspection commands also produce runs.
Typer help and argument-parser errors occur before dispatch and do not create runs.
Use exit code 0 for success, 1 for operational failures, and 2 for configuration
errors. Provide `runs list` and `runs show <id> [--events]` for history inspection.

## Consequences

Operators and scripts share one interface, and CLI integration tests can verify
commands with temporary settings and storage. Application behavior remains reusable
outside Typer. Command families were added in implementation-plan order. The
initial CLI printed the intended report destination; since Step 6.2, finalized
runs also create the report from their public ledger, including failure and
configuration-abort reports.

Source: [CLI interface](../../ARCHITECTURE.md#cli-typer).
