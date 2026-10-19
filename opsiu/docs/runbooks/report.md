# Export active findings

## Purpose and inputs

Run from `opsiu/` after `uv sync`:

```sh
uv run python3 -m opsiu report findings
```

The command exports stored findings whose status is `active`, from the database
selected by settings. It requires the migrated findings and source schema.
`OPSIU_CONFIG` and nested environment overrides work as for the other commands;
paths are relative to the working directory.

This is an export of unreviewed active findings. The approved-only `report final`
command belongs to a later implementation step and is not available yet. This
command does not ingest inputs, rerun checks, or record approval decisions.
Inspect the latest [check](check.md) run when interpreting results: a failed rule
can leave its previous active findings intact.

## Outputs and provenance

Each invocation creates a new run and writes:

- `runs/<run_id>/findings.csv`: UTF-8 CSV, with one row per active finding,
  deterministic ordering, and exact decimal text using a dot separator.
- `runs/<run_id>/findings.md`: a Portuguese summary of counts and signaled
  amounts by rule.
- `manifest.json`, `events.jsonl`, and `log.jsonl`: execution metadata and
  diagnostics. Artifact records contain `path`, `media_type`, `sha256`, and
  `size_bytes`.

The directory prefix follows `paths.runs_dir` when customized. The CLI prints
its run ID first and the intended `report.md` path last. `findings.md` is the
actual findings summary in this step; automatic ledger `report.md` generation
is reserved for Step 6.2.

The CSV preserves each finding's original check and source provenance. Deputy,
invoice, and source details come from the originating expense snapshot rather
than current deputy or source projections. Forced reloads do not replace these
references. The CSV `run_id` and `step_id` identify the finding's original check;
the export run is identified by the output directory and manifest.

Columns appear in this order:

```text
finding_key, rule_id, rule_version, id_documento, severity, amount,
evidence_json, status, numero_deputado_id, nome_parlamentar, sigla_uf,
sigla_partido, ano, mes, fornecedor, cnpj_cpf_digits, numero, data_emissao,
valor_documento, valor_glosa, valor_liquido, url_documento, run_id, step_id,
source_file_id, source_record_number, source_file, source_sha256,
source_size_bytes, record_sha256, code_commit, created_at
```

The displayed header is wrapped for readability; the CSV contains one header
record. `evidence_json` retains the structured rule evidence. `source_sha256`
identifies the original file bytes, while `record_sha256` identifies the
canonical raw record. `source_record_number` locates the expense within that
source snapshot.

## Interpreting totals

Summary amounts are amounts signaled by the rules, not proven or unique losses.
The same document can appear under more than one rule. Rules also use different
amount bases: cap and quota excesses, net expenses, or gross invoice amounts.

[R07](../../.llms/rules/R07-cota-uf.md) and
[R14](../../.llms/rules/R14-liderancas.md) carry accumulated excess across months,
so consecutive findings can overlap. [R09](../../.llms/rules/R09-nota-duplicada.md)
creates a finding for each document in a possible duplicate group, repeating the
same gross invoice amount. Adding those rows cannot establish the amount to
recover. Consult the rule evidence and retain human review before final reporting.

## Failure modes and reruns

A fully migrated database with no active findings is valid: the export contains
the CSV header and an empty findings summary. A missing database or required
schema fails inside a durable run. Inspect its events before using any output:

```sh
uv run python3 -m opsiu runs show RUN_ID --events
```

Use `db migrate` to initialize or update the schema, and use `ingest` and `check`
to populate findings when needed. Exit code `1` indicates operational failures;
configuration errors exit `2`. A successful export exits `0`.

Rerun `report findings` after resolving an error or when a new export is needed.
Every attempt uses a distinct run directory, retaining earlier artifacts. Record
ordering is deterministic; changing the active findings changes the exported
content. The exporter does not alter finding status, evidence, or original
provenance.
