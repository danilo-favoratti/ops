# Check reimbursements against deterministic rules

## Purpose and inputs

Run commands from `opsiu/` after `uv sync`. The checker evaluates current expense
snapshots and dated policies loaded by [ingest](ingest.md). It writes versioned
findings with source, record, run, step, and code provenance. Agents do not decide
whether these rules trigger, and findings still require human review.

```sh
uv run python3 -m opsiu check
uv run python3 -m opsiu check --regras R01,R02
uv run python3 -m opsiu check --regras R07,R14 --dry-run
```

Without `--regras`, all 11 currently registered rules run:

- [R01: vehicle rental](../../.llms/rules/R01-locacao-veiculos.md).
- [R02: taxi, tolls, and parking](../../.llms/rules/R02-taxi-pedagio-estacionamento.md).
- [R03: fuel and lubricants](../../.llms/rules/R03-combustiveis-lubrificantes.md).
- [R04: security services](../../.llms/rules/R04-seguranca.md).
- [R05: courses and events](../../.llms/rules/R05-cursos-eventos.md).
- [R06: housing supplementation](../../.llms/rules/R06-auxilio-moradia.md).
- [R07: accumulated state quota](../../.llms/rules/R07-cota-uf.md).
- [R08: payments to CPF](../../.llms/rules/R08-pagamento-cpf.md).
- [R09: possible duplicate invoices](../../.llms/rules/R09-nota-duplicada.md).
- [R10: issue-to-competence interval](../../.llms/rules/R10-prazo-reembolso.md).
- [R14: leadership quota](../../.llms/rules/R14-liderancas.md).

R11–R13 are not registered yet. Unknown or repeated rule identifiers are
configuration errors. `OPSIU_CONFIG` selects the TOML file; nested `OPSIU_*`
environment overrides take precedence over its values. Paths are relative to the
working directory.

R07 and R14 use `rules.quota_margin_percent`, a finite nonnegative Decimal with
default `5`. For example, this previews a 10% margin without publishing findings:

```sh
OPSIU_RULES__QUOTA_MARGIN_PERCENT=10 uv run python3 -m opsiu check --regras R07,R14 --dry-run
```

The initial policy dates are assumptions, as explained in
[ADR 0008](../adr/0008-policy-effective-date-assumption.md). The quota rules retain
their approximate interpretation and limitations concerning individual roles.

## Execution and outputs

The command starts a run, prepares the database and pending migrations, and
evaluates each selected rule in its own step. Rule SQL uses `despesas_current`;
historical snapshots are retained but are not counted again. Findings are
published transactionally only after the rule completes successfully.

`--dry-run` evaluates candidates and records counts without inserting or
superseding findings. It still creates a run and can apply pending schema
migrations. Publication-time immutable conflicts are not checked by a dry run.

Each invocation prints its run ID first and the intended `report.md` path last.
The run directory contains `manifest.json`, `events.jsonl`, and `log.jsonl`.
The manifest records selected rule versions, source hashes, configuration identity,
and counters for completed, skipped, and failed rules and finding changes.
Automatic `report.md` rendering is scheduled for Step 6.2; inspect current runs
with:

```sh
uv run python3 -m opsiu runs show RUN_ID --events
```

Use [`report findings`](report.md) to export active findings as CSV and a
Portuguese summary. Exporting does not approve the findings.

## Failure modes

A missing required relation causes a recorded skipped rule step, preserving its
previous findings. A missing applicable dated policy, ambiguous source origin,
unknown leadership role, evaluation error, or publication conflict fails that
rule instead. Its attempted finding changes roll back, and other selected rules
can continue. A failed database-preparation step prevents rule evaluation.

A successful complete rule evaluation supersedes prior active findings that are
no longer emitted, including replaced rule versions. Skipped rules, failed rules,
and dry runs never supersede findings.

Exit code `0` means the run completed, which can include skipped rules; inspect
the counters. Exit code `1` indicates operational errors, and `2` indicates an
aborted configuration error. Each failure remains in the run's events.

## Reruns and immutable history

Run the same command again after inspecting and correcting an operational cause.
Identical active findings retain their original evidence and provenance. Forced
ingestion of identical input does not move an existing finding to the new source
snapshot.

The stable identity is `(rule_id, rule_version, id_documento)`. Changed evidence,
amount, severity, or originating record hash conflicts with that identity.
Re-emitting a previously superseded key also fails. The checker rolls back that
rule's attempted changes and preserves the prior findings and reviews; see
[ADR 0009](../adr/0009-immutable-finding-conflicts.md).

Changing the quota margin changes its recorded evidence even if the rule version
is unchanged. A later publishing run can therefore conflict with existing quota
findings. Do not delete or rewrite history to make a rerun pass. Resolution needs
the explicit policy or deliberate rule-version decision described in ADR 0009.
