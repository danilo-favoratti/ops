# Ingest yearly reimbursements

Run commands from `opsiu/` after `uv sync`. Input files use the published
`{"dados": [...]}` envelope and names `Ano-YYYY.json`. The configured defaults
read `../data/Ano-2023.json` through `../data/Ano-2026.json`; source files remain
unmodified and must never be committed.

```sh
uv run python3 -m opsiu ingest
uv run python3 -m opsiu ingest --anos 2023-2026
uv run python3 -m opsiu ingest --anos 2026 --force
```

`--anos` accepts one calendar year or an inclusive range. Without it, the command
uses `[years]` from settings. `OPSIU_CONFIG` selects a TOML file; nested environment
overrides such as `OPSIU_PATHS__DATA_DIR` and `OPSIU_PATHS__DB_PATH` take precedence.
Paths are relative to the current working directory.

The command first starts a durable run, applies pending migrations, loads dated
policy rows, then opens one step per yearly file. It prints the run ID first and
the generated run-report path last. The Portuguese `report.md` summarizes steps,
durations, counters, and failures; the manifest and event log retain structured
provenance. Inspect a run with:

```sh
uv run python3 -m opsiu runs list
uv run python3 -m opsiu runs show RUN_ID --events
```

Each source is read once, with bounded buffers and batches of at most 10,000
normalized expenses. The exact input bytes produce the file SHA-256; every raw
record also has a canonical SHA-256. The row year must match the requested file
year. A malformed record, incomplete JSON, or unreadable source fails its step
and leaves the prior current snapshot intact. Later years still run. Exit code
`1` indicates a run with errors; invalid configuration or year selection exits
`2` and leaves an aborted run manifest.

Publication commits the source metadata, all its expenses, and the rebuilt deputy
table atomically. Until the final file hash is available, batches are staged in a
database transaction. An unchanged current hash skips publication and rolls back
staging. Determining that a file is unchanged still requires reading it completely;
the process never loads a full yearly file into memory. `--force` publishes a new
snapshot even when its hash is unchanged.

Missing, null, or blank `valorGlosa` is retained as unknown (`NULL`), not zero.
Explicit zero remains zero; negative net amounts remain negative, and gross/net
values are not used to infer a deduction. Invalid monetary strings and missing
required identifiers still fail the complete yearly step. Normalization errors
identify the record's one-based position in the source. See
[ADR 0011](../adr/0011-missing-deductions.md).

`source_files` and `despesas` retain all published snapshots. Expense identity
within a snapshot is `(source_file_id, source_record_number)`, preserving even
repeated document IDs and exact source order. Source, run, step, and record hashes
are stored with each expense. Queries for current audit work **must use
`despesas_current`**, which selects the newest published source per year using
ingestion time and source ID. Querying the historical `despesas` table would count
forced and changed reloads more than once. A successfully published empty source
replaces that year's current rows with an empty set without deleting history.

`deputados` is a current projection rebuilt from `despesas_current`. It selects
each identity's latest expense by competence, issue date, document ID, source ID,
and record position, in that order. Leadership requires both an empty normalized
CPF and `siglaUF = "NA"`. This projection is replaceable; the originating expense
history remains immutable.

For an isolated smoke test, copy the synthetic `Ano-2099.json` from
`tests/fixtures/mini_dataset/` into a temporary input directory, configure temporary
database and run paths, and invoke `ingest --anos 2099`. It loads 200 expenses and
15 identities, including one leadership account. A second invocation skips the
file; `--force` adds historical rows while leaving 200 current expenses. The
[fixture documentation](../../tests/fixtures/mini_dataset/README.md) enumerates
the synthetic rule cases. Use the explicit offline rule selection from the
[project walkthrough](../../README.md) when external snapshots are unavailable.

After correcting a missing or malformed input, rerun the selected year. A failed
source does not become current, so it does not require `--force` to retry. Do not
delete the previous database or source history to bypass a parsing error. For
run-report publication failures and exit-code interpretation, see
[run inspection](runs.md).
