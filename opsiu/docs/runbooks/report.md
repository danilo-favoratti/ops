# Export findings and approved final reports

## Purpose and inputs

Run from `opsiu/` after `uv sync`:

```sh
uv run python3 -m opsiu report findings
```

The command exports stored findings whose status is `active`, from the database
selected by settings. It requires the migrated findings and source schema.
`OPSIU_CONFIG` and nested environment overrides work as for the other commands;
paths are relative to the working directory.

`report findings` includes active findings without filtering human decisions.
Use `report final` to export only active findings whose latest human decision is
`aprovado`, as described below. Neither command ingests inputs, reruns checks,
or records approval decisions.
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

## Final report over approved findings

After recording human decisions through [review](review.md), run:

```sh
uv run python3 -m opsiu report final
```

The final export selects active findings whose latest human decision is
`aprovado`. A later `rejeitado` or `revisar` decision excludes the finding, and
superseded findings are excluded even if they were previously approved. An
agent recommendation never supplies approval. The current decision is ordered
by decision time and decision ID, matching the review view.

Selection reads one database snapshot through a single cursor, retaining the
original expense context and each selected approval. It groups output by deputy
while buffering only that deputy's findings. This command does not run agents,
change decisions, or migrate the database; apply `db migrate` separately when
the required schema is missing.

Each new export run writes these immutable artifacts:

- `final.csv`: UTF-8 CSV with one row per selected finding and exact decimal
  amounts. It includes the 32 original finding/provenance columns above, followed
  by `decision_id`, `decision`, `reviewer`, `comment`, `decided_at`,
  `decision_run_id`, `decision_step_id`, and `pareceres_json`.
- `final.md`: a Portuguese summary with counts and signaled amounts by rule
  and version, plus links to the deputy reports.
- `deputado-{ID}.md`: a Portuguese report for each deputy with selected findings,
  rule evidence, human approval details, original provenance, and opinion
  summaries. The filename uses the numeric deputy identifier.

All files live directly in `runs/<run_id>/`, or the configured run-directory
prefix. Their paths, media types, SHA-256 hashes, and byte sizes are recorded as
artifacts in the manifest and events. The CLI's final `report.md` path remains
the reserved ledger-report path described above; `final.md` is the actual final
audit summary.

The approval columns identify the selected human decision and its own run/step.
They do not replace the finding's original `run_id`, `step_id`, source hashes,
or record hash. `pareceres_json` retains all successful `parecer` history for the
selected finding, with annotation/trace identities, model and prompt version,
output, and origin metadata. Invoice-reader outputs and consolidated deputy
agent summaries are not included as opinions. An approved finding without an
opinion remains eligible and has an empty opinion list.

Opinion recommendations, including `descartar`, remain advisory and do not
override the selected approval. Scripted opinions are simulated responses;
exporting them does not turn them into independent evidence. Human approval
also does not eliminate the overlap and amount-basis limitations below.

A migrated database with no eligible approvals produces a header-only
`final.csv` and an empty Portuguese `final.md`, with no deputy files. Successful
runs record `final.exported` and counters `findings_exported`,
`deputies_exported`, `rules_exported`, and `artifacts_written`.

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

The same failure and exit-code behavior applies to `report final`. Incomplete
artifact writes are removed; already completed artifacts remain recorded if a
later export stage fails. Inspect the run status before treating a set of files
as a completed report.

Rerun either export after resolving an error or when a new report is needed.
Every attempt uses a distinct run directory, retaining earlier artifacts. Record
ordering is deterministic. Changed active findings, current decisions, or opinion
history can change a later final export, while earlier runs retain their original
snapshot. The exporters do not alter finding status, decisions, evidence, or
original provenance.
