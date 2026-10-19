# Inspect runs and their generated reports

Run from `opsiu/` after `uv sync`:

```sh
uv run python3 -m opsiu runs list
uv run python3 -m opsiu runs show RUN_ID
uv run python3 -m opsiu runs show RUN_ID --events
```

Replace `RUN_ID` with the UUID printed by a command. `runs list` has no filters.
`runs show` accepts one run ID and the optional `--events` flag. Both commands
use the configured database and run directory without applying migrations.

## List and inspect history

`runs list` combines manifests from `paths.runs_dir` with database ledger rows
when that schema is available, ordered by start time. File records take
precedence for duplicate run IDs because they retain extended metadata. Each
line shows the ID, command, and lifecycle status. File-only bootstrap and
configuration-failure runs remain visible after the database is initialized.

`runs show` displays the requested command, status, steps, and failure events.
With `--events`, it also prints each ordinary structured event as JSON. It prints
the target run's `report.md` path. It does not read private agent trace bodies.
It prefers the file manifest and falls back to the database ledger if the
manifest is absent.

The inspection itself creates a new run. Its ID is the first output line and
its own report path is the last, distinct from the target run's path inside
`runs show`. An inspection may appear as `running` in its own database-backed
listing; its final state is written when the command ends. Viewing an old run
does not change that run's manifest, events, report, or business artifacts.

An unknown or malformed run ID fails the inspection with exit code `1`, leaving
the new inspection run's diagnostics. If the database is missing, run inspection
still works from manifests. If opening an existing database fails, the command
fails and retains its own file-only diagnostics; it does not silently ignore the
database error.

## Files created when a run finishes

Each command execution creates `runs/<run_id>/`, or the configured
`paths.runs_dir` prefix, with these files:

- `events.jsonl`: append-only ordinary events, including step failures and skips.
- `log.jsonl`: structured log entries for those events, also emitted to stderr.
- `manifest.json`: finalized command arguments, configuration hash, code and
  runtime versions, status, steps, counters, and collected provenance metadata.
- `report.md`: a generated Portuguese summary of the command, start/end times,
  duration, step outcomes, counters, failure/skip messages, and produced artifacts.

The report links registered artifacts using local filenames and includes their
recorded sizes and hashes. Artifact contents are not read again to render it.
Finding keys and agent trace IDs referenced by ordinary events are listed with
links to the event log. Prompt bodies, private agent requests/responses, and
private trace tables are not loaded into this report. Preserve the database when
the full private trace is needed for an authorized audit.

`report.md` is a ledger file, like the manifest, rather than an exported business
artifact. It has no self-hash entry and does not increment `artifacts_written`.
It is separate from `findings.md`, `final.md`, and deputy summaries produced by
[report exports](report.md). Files are created exclusively; a finished run's
report is never overwritten by another command.

Reports are also generated for file-only runs, failed steps, configuration
aborts before any step, and local web requests. The long-running `review` launch
run receives its final report after the server stops; individual web requests
finish their own reports independently. A process killed before finalization
may leave only partial ledger files. Runs created before automatic reports were
introduced are not backfilled, and `runs show` still prints their expected report
path even if no such file exists.

## Status, exit codes, and report publication

Command lifecycle statuses are:

- `running`: the run has not finished.
- `completed`: the command finished without a failed step; skipped steps can
  still be present and should be inspected.
- `completed_with_errors`: an ordinary exception or failed step was recorded.
  Other independent steps can still have succeeded.
- `aborted`: configuration or a nonstandard interruption prevented completion.

Normal CLI completion exits `0`, recorded operational failures exit `1`, and
aborted configuration errors exit `2`. Validation performed inside a command
leaves an audited run. `--help`, unknown options, and parser-level type errors
occur before command execution, so they do not create run directories.

Manifest publication precedes report rendering. If creating or rendering
`report.md` fails, the CLI exits `1` and preserves the completed manifest. Any
partially created new report is removed when filesystem cleanup succeeds; an
existing report at the same path is never replaced. The manifest and database
status already record the command outcome and can therefore remain `completed`:
that status alone does not prove the report file was published. The publication
error is raised after lifecycle finalization and is not appended as a new
`run.failed` event. Check the process exit code and expected files alongside the
stored status.

After fixing a storage or rendering problem, rerun the original command to
create a fresh run. Existing manifests and business artifacts remain evidence
of the earlier attempt; there is no report-backfill command. When investigating
an incident, retain the relevant run directory and use the
[post-mortem template](../postmortem-template.md).

If settings cannot be loaded, the fallback run directory is
`OPSIU_PATHS__RUNS_DIR` when set, otherwise `runs/` in the working directory.
The aborted manifest records an unknown configuration hash because no valid
settings were resolved.
