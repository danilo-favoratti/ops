# Command runbooks

Run commands from `opsiu/` using `uv run python3 -m opsiu`. Every command prints
its run ID first and its report path last. The run's `manifest.json`,
`events.jsonl`, and `log.jsonl` provide reproducibility and failure evidence.
Automatic Markdown run reports are scheduled for implementation step 6.2.

Each runbook will document purpose, inputs, outputs, failure modes, and how to
rerun the command. The implementation plan adds the command-specific files as
their use cases become available; the final documentation pass completes them.

- `db migrate` and `db status`: initialize and inspect schema migrations;
  runbook planned as `db.md`.
- `runs list` and `runs show <id> [--events]`: inspect execution history;
  runbook planned as `runs.md`.
- [`ingest`](ingest.md): stream yearly expense files into the database.
- [`check`](check.md): apply deterministic rules with versioned finding history.
- [`fetch tse`, `fetch cnpj`, and `fetch pdf --sample`](fetch.md): load external
  datasets for R11–R13 and capture sampled invoice PDFs with versioned text
  extraction and verified cache reuse.
- [`explain`](explain.md): append bounded, traced annotations to sampled
  findings using scripted responses by default or explicitly enabled Codex.
- `review`: launch the local approval application; planned as `review.md`.
- [`report findings`](report.md): export active, unreviewed findings as CSV and
  a Portuguese summary. The approved-only `report final` command is planned.

For incidents, use the [post-mortem template](../postmortem-template.md). Design
decisions are recorded in [ADRs](../adr/); version changes are recorded in the
[changelog](../CHANGELOG.md).
