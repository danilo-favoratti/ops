# Command runbooks

Run commands from `opsiu/` after `uv sync`, using `uv run python3 -m opsiu`.
Each command execution prints its run ID first and its run-report path last.
On completion, the run directory contains `manifest.json`, `events.jsonl`,
`log.jsonl`, and the Portuguese `report.md`. This includes failed and aborted
runs. Help and CLI parsing errors occur before a command starts and create no
run. [Run inspection](runs.md) explains lifecycle states, exit codes, and report
publication failures.

- [`db migrate` and `db status`](db.md): initialize and inspect schema migrations.
- [`runs list` and `runs show <id> [--events]`](runs.md): inspect execution history
  and generated run reports.
- [`ingest`](ingest.md): stream yearly expense files into the database.
- [`check`](check.md): apply deterministic rules with versioned finding history.
- [`fetch tse`, `fetch cnpj`, and `fetch pdf --sample`](fetch.md): load external
  datasets for R11–R13 and capture sampled invoice PDFs with versioned text
  extraction and verified cache reuse.
- [`explain`](explain.md): append bounded, traced annotations to sampled
  findings using scripted responses by default or explicitly enabled Codex.
- [`review [--port 8765]`](review.md): inspect original evidence and record
  append-only human decisions through the local Portuguese interface.
- [`report findings` and `report final`](report.md): export all active findings
  or only those currently approved, with provenance and Portuguese summaries.

Configuration comes from `opsiu.toml` in the current directory, or the file named
by `OPSIU_CONFIG`. Nested environment variables such as
`OPSIU_PATHS__DB_PATH` override individual values. Relative paths are resolved
from the current working directory, including when using another TOML file.
An explicitly selected missing or invalid file fails configuration; it does not
silently fall back to defaults. The resolved configuration hash is retained in
every run manifest.

The usual workflow is ingest → check → optional PDF capture and explanations →
human review → final export. Load TSE and filtered Receita snapshots before
checking rules that need them. An isolated, offline fixture walkthrough appears
in the [project README](../../README.md). Default agent mode is scripted;
network dataset fetches and explicitly enabled live agent calls are separate
operations described in their runbooks.

For incidents, use the [post-mortem template](../postmortem-template.md). Design
decisions are recorded in [ADRs](../adr/); version changes are recorded in the
[changelog](../CHANGELOG.md).
