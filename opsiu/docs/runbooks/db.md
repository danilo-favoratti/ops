# Initialize and inspect the database schema

Run from `opsiu/` after `uv sync`:

```sh
uv run python3 -m opsiu db status
uv run python3 -m opsiu db migrate
uv run python3 -m opsiu db status
```

Both commands use `paths.db_path`, defaulting to `../data/opsiu.duckdb`.
`OPSIU_CONFIG` selects a TOML file; `OPSIU_PATHS__DB_PATH` can override just the
database path. Relative paths use the working directory. Neither command accepts
additional options beyond `--help`.

## Inspect migrations

`db status` lists bundled and previously applied migration IDs in lexical order.
Each line reports `pendente` or its stored application timestamp. It does not
apply migrations. When the database does not exist, it lists all bundled
migrations as pending without creating the database. This inspection still
creates a file-only audit run.

When a database with the run-ledger schema exists, the inspection run is also
recorded there. Thus the command leaves migration state unchanged while retaining
its own audit history. An existing empty database or one without the ledger
tables is inspected through a file-only run until those tables are available.

## Apply migrations

`db migrate` creates the database and its parent directory if necessary, then
applies pending bundled SQL files in lexical order. Each migration's SQL and
`schema_migrations` entry commit in one transaction. Rerunning the command skips
already recorded IDs and reports `Nenhuma migração pendente.` when up to date.

The command reports each newly applied ID, records `db.migrated`, and stores the
`migrations_applied` counter. This command initializes schema only; it does not
ingest expenses, load dated policy rows, fetch datasets, or evaluate rules.
Use [ingest](ingest.md) to load the local expenses and policy data.

Other commands that prepare data (`ingest`, `check`, `fetch`, `explain`, and
`review`) also apply pending migrations. The report commands and run inspectors
do not. `report findings` and `report final` therefore need their required schema
to be present before export.

## Failure and upgrade handling

A failing migration rolls back that migration's changes and history entry.
Earlier migrations completed in the same invocation remain committed. If the
run-ledger schema became usable before the failure, the run is retained in both
the database and files; otherwise, the file record remains available. A failed
initial connection or migration can leave an empty database file, but it does
not erase the run directory.

Inspect the run's `report.md` and structured events:

```sh
uv run python3 -m opsiu runs show RUN_ID --events
```

Resolve permissions, database locks, or the reported migration problem before
retrying. Stop the local review server before opening another process that needs
the same DuckDB file. Preserve the database and run files when investigating an
upgrade; do not delete audit rows or edit applied migrations to force a retry.
Schema corrections belong in a new migration with a changelog entry.

For a successful invocation the exit code is `0`; operational failures use `1`,
and configuration errors use `2`. See [run inspection](runs.md) for the separate
report-publication failure case. Every executed command has a distinct run ID,
even when no migration was pending.
