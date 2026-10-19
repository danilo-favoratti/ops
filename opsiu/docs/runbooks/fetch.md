# Fetch and replay external evidence

## Available source and inputs

The current command loads TSE candidates for the 2024 municipal and 2026 general
elections. CNPJ and PDF fetch commands belong to later implementation steps.
Run from `opsiu/` after `uv sync`:

```sh
uv run python3 -m opsiu fetch tse --ano 2024
uv run python3 -m opsiu fetch tse --ano 2026
```

The primary source catalogs are TSE's
[2024 candidates](https://dadosabertos.tse.jus.br/dataset/candidatos-2024) and
[2026 candidates](https://dadosabertos.tse.jus.br/dataset/candidatos-2026).
The required schema and ordinary first-round selection come from the official
candidate exports linked by these catalogs.

`--ano` selects 2024 or 2026. Downloads use `external.tse_2024_url` or
`external.tse_2026_url` from settings. `OPSIU_CONFIG` selects a TOML file, and
environment overrides such as `OPSIU_EXTERNAL__TSE_2024_URL` take precedence.
Use `OPSIU_PATHS__EXTERNAL_DIR`, `OPSIU_PATHS__DB_PATH`, and
`OPSIU_PATHS__RUNS_DIR` to isolate storage when testing. Relative paths are
resolved against the working directory.

## Raw snapshots and local replay

Each successful download creates an immutable indexed snapshot under
`paths.external_dir/tse_2024/<snapshot_id>/` or
`paths.external_dir/tse_2026/<snapshot_id>/`. It contains the raw `payload` and a
`manifest.json` with URL, safe response headers, SHA-256, byte count, fetch time,
and original run and step identifiers. Loaders consume the saved payload, so
parsing can be repeated without another HTTP request.

```sh
uv run python3 -m opsiu fetch tse --ano 2024 --replay
uv run python3 -m opsiu fetch tse --ano 2024 --snapshot-id SNAPSHOT_ID
```

`--replay` selects the latest indexed raw snapshot for that election year.
`--snapshot-id` selects one exact local snapshot belonging to that year's source.
The options are mutually exclusive and make no HTTP request. Replay validates
the local manifest and payload against the snapshot index before loading them.

The latest raw download and the latest successful candidate import are different
concepts. A successfully downloaded payload can fail parsing and remain available
as evidence. In that case, `--replay` selects that same latest raw payload again;
use an explicit known-good snapshot ID when deliberately replaying older input.

## Candidate loading and history

The loader reads semicolon-separated candidate CSV in Latin-1 or UTF-8 with a
BOM. ZIP payloads are read as streams without extracting members to disk. When
a national `BRASIL` candidate member exists, it is used instead of also loading
the state copies; otherwise the state members and any federal-scope `BR` member
are read. `BR` is not the national `BRASIL` aggregate.

Required columns are `SQ_CANDIDATO`, `ANO_ELEICAO`, `NR_CPF_CANDIDATO`,
`NM_CANDIDATO`, `SG_UF`, `DS_CARGO`, `DT_ELEICAO`, `NR_TURNO`, and
`CD_TIPO_ELEICAO`. Keep only the selected policy election date (`2024-10-06` or
`2026-10-04`), round `1`, and type `2` (`ELEIÇÃO ORDINÁRIA`). Other rounds and
supplementary elections are outside this import. Missing required columns and
malformed values fail rather than guessing dates or election types.

Names are normalized consistently for candidate matching, while each stored
candidate retains its source member and record position. Repeated rows for the
same `SQ_CANDIDATO` are deduplicated when their candidate metadata agrees;
conflicting metadata for that identity fails publication. Migration `0005a_tse`
adds immutable candidate and import history with snapshot, run, and step origins.
`tse_imports_current` selects the latest successfully published import per year,
and `tse_candidatos_current` exposes only its candidates to the rule.

Import publication is atomic. Download or parse failures leave the previous
current import unchanged. A valid empty import makes that year's current
candidate set empty while retaining its previous history. An input whose SHA-256
matches the current import is skipped and retains the existing candidate origin.
A network invocation can still create a new raw snapshot before this skip is
determined.

## Runs and failure diagnosis

Every invocation prints its run ID first and the intended `report.md` path last.
Inspect `manifest.json`, `events.jsonl`, and `log.jsonl` for the selected year,
snapshots, import outcome, and failures. Automatic run-report rendering remains
scheduled for Step 6.2.

```sh
uv run python3 -m opsiu runs show RUN_ID --events
```

Unsupported years or conflicting replay options are configuration errors.
Unavailable HTTP sources, corrupt snapshots, malformed candidate files, or
database failures produce a recorded failed step. Exit code `1` denotes an
operational failure; configuration errors exit `2`; a completed run exits `0`.

After correcting an operational issue, rerun the command or replay the intended
snapshot explicitly. Do not alter raw snapshot files to repair them: replay
checks their hashes, and earlier snapshots and imports are retained as evidence.

## Using the imported evidence

Load the corresponding reimbursement years with [ingest](ingest.md), then run:

```sh
uv run python3 -m opsiu check --regras R11
uv run python3 -m opsiu report findings
```

R11 evaluates the pre-election publicity window using the latest successful
candidate import for each election year. Candidate matching and its limitations
are documented in [R11](../../.llms/rules/R11-divulgacao-eleitoral.md).
A relevant in-window expense with no successful import for its election year
fails the rule; a successfully imported empty candidate set is a valid no-match
result. These outcomes preserve the distinction between unavailable evidence
and an imported dataset with no matching candidate.

Importing a different snapshot can change a finding's candidate evidence without
changing `(rule_id, rule_version, id_documento)`. A subsequent check can therefore
fail with the immutable-conflict behavior in
[ADR 0009](../adr/0009-immutable-finding-conflicts.md). Preserve the earlier finding
and review history rather than overwriting them. `report findings` remains an
export of active unreviewed findings; it does not grant approval.
