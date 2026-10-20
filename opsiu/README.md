# OPSIU

OPSIU audits Brazilian parliamentary reimbursements (CEAP) with deterministic
rules, optional AgentRig annotations, and local human review. The default audit
period is **2023–2026**. Only active findings whose latest human decision is
`aprovado` enter the final report.

Rules, agent prompts, review screens, and generated reports are in Portuguese.
Code and operational documentation are in English. The policy source is
[cota-parlamentar.md](cota-parlamentar.md); the implemented rule versions and
their limitations are documented in [.llms/rules](.llms/rules/README.md).

## Install and configure

Use Python 3.12 or newer and uv. From the repository root:

```sh
cd opsiu
uv sync --locked
uv run python3 -m opsiu --help
```

The single installable package lives in `src/opsiu/`. Run commands from this
directory so the checked-in [opsiu.toml](opsiu.toml) and relative paths resolve
as intended:

- Source files: `../data/Ano-2023.json` through `../data/Ano-2026.json`.
- DuckDB: `../data/opsiu.duckdb`.
- External snapshots and PDFs: `../data/external/`.
- Run evidence and reports: `runs/<run_id>/`.

Source data, databases, external caches, and generated runs are git-ignored.
Do not commit the yearly data files. Ingestion streams each source file; tests
use the small synthetic fixture rather than the production dataset.

Set `[review].reviewer` to the local reviewer's name. Defaults include
`sampling.per_rule = 20`, `agents.max_calls_per_run = 20`, and a quota margin of
5 percent. The configured live model is `gpt-5.6-terra`; offline annotations are
explicitly recorded as model `scripted`.

`OPSIU_CONFIG` selects a different TOML file. Nested environment variables such
as `OPSIU_REVIEW__REVIEWER` and `OPSIU_AGENTS__MAX_CALLS_PER_RUN` override TOML.
Relative paths still resolve from the working directory, including when using
a custom configuration file.

## Quick start

With the configured yearly source files available:

```sh
uv run python3 -m opsiu db migrate
uv run python3 -m opsiu ingest
uv run python3 -m opsiu check --regras R01,R02,R03,R04,R05,R06,R07,R08,R09,R10,R14
uv run python3 -m opsiu report findings
```

This first check uses rules that can run from the expense data and published
policy tables. R11 requires TSE coverage; R12 and R13 require filtered Receita
CNPJ snapshots. Follow the [external-source runbook](docs/runbooks/fetch.md)
before running those rules. An unsuccessful rule leaves its earlier findings
intact, so inspect the check's run report before using the results.

Generate a small offline annotation sample and review the findings:

```sh
uv run python3 -m opsiu explain --regras R08 --amostra 2
uv run python3 -m opsiu review
```

Open `http://127.0.0.1:8765/findings`. Inspect the original expense, deterministic
evidence, rule text, available invoice text, and agent opinions. Record an
approval, rejection, or request for further review.

Open `http://127.0.0.1:8765/graph` to explore connections between parliamentarians,
reimbursements and suppliers. This view covers all current ingested accounting
rows, including adjustments, independently of findings. It loads small pages
on demand and includes search and memory/rendering diagnostics; see the
[graph runbook](docs/runbooks/graph.md).

Stop the local server when finished, then export:

```sh
uv run python3 -m opsiu report final
uv run python3 -m opsiu runs list
uv run python3 -m opsiu runs show RUN_ID --events
```

Replace `RUN_ID` with an ID printed by a command. `report final` writes
`final.csv`, `final.md`, and `deputado-{ID}.md` inside its new run directory.
An empty approval set produces a header-only CSV and an empty-result summary.
`report findings` exports all active findings without filtering human decisions.

PDF capture is optional and explicitly requested with `fetch pdf --sample`.
The review page does not download documents or call agents automatically.
`explain --consolidar` creates deputy summaries from existing explanation
opinions; it does not annotate findings first or approve anything.

## Agent execution

Without `AGENTRIG_RUN_LIVE=1`, annotation and consolidation use scripted AgentRig
runtimes. Their responses are simulated fixtures for exercising the workflow,
not an independent assessment of the invoices.

Live execution requires the literal `AGENTRIG_RUN_LIVE=1` gate, a configured
Codex environment, and a positive call budget. A custom `agents.live_env_var`
adds a gate; it does not replace the literal one. Calls use strict typed codecs,
a read-only sandbox, denied network access, one turn, and zero tool calls.
Failed attempts count against the shared per-run budget; cache hits do not.
See [explain](docs/runbooks/explain.md) for sampling, retries, and live setup.

Agents annotate findings. Their recommendation, confidence, or deputy ranking
never replaces the deterministic rule result or a human approval.

## Audit history and reports

Each command invocation and review request has a UUIDv7 run ID. A finished run
has `manifest.json`, `events.jsonl`, `log.jsonl`, and a Portuguese `report.md`
with duration, step states, counters, failures, and artifact links. Commands
print the run ID first and the report path last. Read
[runs](docs/runbooks/runs.md) for failure and recovery details.

Findings have immutable identity `(rule_id, rule_version, id_documento)`.
Supersession is an appended event; PDF references, opinions, summaries, and
decisions remain attached to the original record. New human decisions and
forced agent annotations append history. Each export writes a fresh directory.
Conflicting evidence for an existing finding key fails the rule step rather
than replacing history; see [ADR 0009](docs/adr/0009-immutable-finding-conflicts.md)
and [ADR 0010](docs/adr/0010-append-only-finding-lifecycle.md).

Run reports use public ledger metadata. Full agent inputs, prompts, responses,
and trace chunks are private records in DuckDB. The final audit export preserves
original source hashes, finding provenance, the selected approval, and successful
explanation history. Scripted opinions remain labeled as simulated.

## Interpretation limits

Reported amounts are rule-specific signals, not proven losses or unique sums
recoverable. Accumulated quota excesses and duplicate-invoice findings can
overlap. R10 is suspended without actual supply/submission dates, and R14 is
suspended without pooled leadership entitlement history. R12 requires a valid
numeric CNPJ and an ownership qualification, but a shared surname still does not
prove kinship. R13 uses selected registered activities as invoice-review leads;
the company name alone no longer triggers it. Blank PDF extraction is flagged
`needs_ocr`; OCR is not implemented.

R01 and R05 use researched cap schedules for 2023–2026, with transition and
entitlement uncertainty recorded explicitly. Other policy histories, including
R07's state quotas, still retain the initial effective-date assumptions and need
research. See [ADR 0008](docs/adr/0008-policy-effective-date-assumption.md),
[ADR 0014](docs/adr/0014-researched-policies-and-rule-suspension.md) and the
individual rule documents before interpreting findings.

## Development and documentation

The four layers are domain, application, adapters, and interfaces, with inward
dependencies and typed ports. See [ARCHITECTURE.md](ARCHITECTURE.md),
[IMPLEMENTATION.md](IMPLEMENTATION.md), the [runbook index](docs/runbooks/README.md),
[ADRs](docs/adr/), [changelog](docs/CHANGELOG.md), and
[post-mortem template](docs/postmortem-template.md).

Run all required offline gates from `opsiu/`:

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -m "not eval"
```

Tests use temporary databases, local HTTP fixtures, synthetic expenses, and
scripted agents. No production yearly file is read in full by the tests.

Run the recommendation evaluation separately:

```sh
AGENTRIG_RUN_LIVE=0 uv run pytest -m eval -s
```

The default budget scores up to 20 of 50 synthetic cases. For the complete
scripted baseline, set `OPSIU_AGENTS__MAX_CALLS_PER_RUN=50` on the same command.
Every evaluation creates an isolated database and source fixture inside its run,
with private agent traces, `eval_results.json`, `eval.md`, and the normal run
ledger. It does not use the production database. See [the evaluation guide](evals/README.md)
for label assumptions, coverage, scoring, and the explicit live gate.

The [GitHub Actions workflow](../.github/workflows/opsiu.yml) runs on every push
and pull request, using Python 3.12, a locked uv install, cached dependencies,
and the four offline gates above. Live evaluation is excluded and its gate is
disabled. No Codex credentials or production data are needed for CI.
