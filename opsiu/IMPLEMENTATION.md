# OPSIU — implementation phases and steps

Hand-off guide for coding agents. Each step is one task for one agent and ends
in one commit. Steps inside a phase run in order; phases run in order unless
marked parallel.

## Brief to paste at the top of every task

```text
You are implementing one step of the OPSIU project (CEAP reimbursement audit).
Read, in this order: opsiu/ARCHITECTURE.md, opsiu/PLAN.md, opsiu/IMPLEMENTATION.md
(your step), and opsiu/cota-parlamentar.md when the step touches rules.

Hard constraints:
- Python 3.12+, package `opsiu` under opsiu/src/opsiu, managed by uv (pyproject.toml).
- Layers: domain has no I/O and imports nothing from application/adapters/interfaces;
  application depends on domain and on ports (Protocols) only; adapters implement
  ports; interfaces are thin.
- Every command runs inside a run (application/runs.py). No side effects outside a run.
- Never overwrite: findings are versioned, decisions and traces are append-only.
- mypy --strict, ruff check, ruff format, pytest must pass. Add tests for what you build.
- Rules, prompts, and user-facing report text are in Portuguese. Code and docs in English.
- Do not touch data/ (ignored by git). Use tests/fixtures/mini_dataset for tests.
- Do only this step. If something is missing from an earlier step, stop and report it.
- Finish with exactly one commit using the message given in the step.
```

Acceptance for every step, beyond what the step lists: gates pass
(`uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -m "not eval"`),
no new TODOs without an issue reference, and `docs/CHANGELOG.md` updated when
a rule, schema, or prompt version changes.

---

## Phase 0 — Project skeleton and run ledger

Goal: an installable package with settings, a migrated DuckDB, and a run ledger
that every later step plugs into. Nothing domain-specific yet.

### Step 0.1 — Package and tooling

- Create `opsiu/pyproject.toml` (name `opsiu`, `requires-python >= 3.12`,
  src layout, dependencies: `duckdb`, `pydantic`, `pydantic-settings`, `typer`,
  `httpx`, `pypdf`, `fastapi`, `uvicorn`, `jinja2`, `python-multipart`,
  `agentrig @ git+https://github.com/mtmoreira/agentrig.git@cc585b46f5365cf1997f71704302d477dd5a7685`,
  `openai-codex==0.144.4`; dev group: `pytest`, `ruff`, `mypy`, `httpx` test client).
- Configure ruff (line length 88, isort), mypy strict with `mypy_path = src`,
  pytest markers `unit`, `contract`, `integration`, `eval`.
- `src/opsiu/__init__.py` with `__version__`; `python3 -m opsiu --help` prints
  a Typer app with no commands yet.
- `uv lock`; `uv sync` works from `opsiu/`.
- Add `opsiu/.gitignore` for `.venv/`, `runs/`, `data/`, `*.duckdb`.
- Acceptance: gates pass on an empty test suite with one smoke test importing `opsiu`.
- Commit: `Scaffold the opsiu package with uv, ruff, mypy, and pytest.`

### Step 0.2 — Settings

- `src/opsiu/settings.py`: pydantic-settings model with sections `paths`
  (`data_dir`, `db_path`, `runs_dir`, `external_dir`), `years` (`from`, `to`),
  `sampling` (`per_rule`), `agents` (`model`, `max_calls_per_run`,
  `live_env_var = "AGENTRIG_RUN_LIVE"`), `review` (`reviewer`), `external`
  (TSE and Receita URLs).
- Load `opsiu.toml` from cwd or `OPSIU_CONFIG`; env overrides with prefix
  `OPSIU_` and `__` nesting.
- `settings_hash()` returns sha256 of the resolved settings as canonical JSON.
- Ship a default `opsiu/opsiu.toml` matching PLAN.md (years 2023–2026, sampling 20).
- Tests: defaults load; env override wins; hash is stable across key order.
- Commit: `Add typed settings loaded from opsiu.toml with env overrides.`

### Step 0.3 — DuckDB connection and migrations

- `adapters/duckdb/connection.py`: open/create database at `paths.db_path`,
  context manager, read-only option.
- `adapters/duckdb/migrations/0001_init.sql`: `schema_migrations`, `runs`,
  `steps`, `events`, `source_files`. Column lists per ARCHITECTURE.md; `runs`
  holds `run_id`, `command`, `args_json`, `settings_hash`, `code_commit`,
  `code_dirty`, `started_at`, `finished_at`, `status`, `counters_json`.
- Migration runner: apply files in lexical order not yet in
  `schema_migrations`, each in a transaction, record id and timestamp.
- CLI `db migrate` and `db status`.
- Tests: fresh db gets all migrations; second run applies none; a failing
  migration leaves no partial record.
- Commit: `Add DuckDB connection and ordered SQL migrations with a CLI.`

### Step 0.4 — Run ledger

- `application/ports.py`: `Ledger` protocol (`start_run`, `start_step`,
  `finish_step`, `emit`, `finish_run`), `Clock`, `IdGenerator`.
- `application/runs.py`: `Run` context manager producing UUIDv7 ids, steps as
  nested context managers, `emit(kind, attributes)`, failure capture inside a
  step recorded as `step.failed` with normalized error, run status
  `completed`, `completed_with_errors`, or `aborted`.
- `adapters/duckdb/ledger.py` implementing `Ledger`; `adapters/files/jsonl_log.py`
  appending the same events to `runs/<run_id>/events.jsonl`; a composite that
  writes to both.
- Manifest: on finish write `runs/<run_id>/manifest.json` (command, args,
  settings hash, git commit and dirty flag via `git rev-parse` fallback to
  `unknown`, python version, package version, counters).
- Structured JSON logging bound to `run_id`/`step_id`, file `runs/<run_id>/log.jsonl`
  plus stderr at INFO.
- CLI `runs list` and `runs show <id> [--events]`.
- Tests: a run with one failing step finishes `completed_with_errors`, events
  are in both sinks, manifest fields present; `runs show` renders.
- Commit: `Add the run ledger with steps, events, manifests, and run CLI.`

### Step 0.5 — ADRs and docs scaffold

- `docs/adr/0001-layered-single-package.md` … `0007-fastapi-in-package.md`,
  one per decision in ARCHITECTURE.md, using a short ADR template (context,
  decision, consequences).
- `docs/CHANGELOG.md` (Keep a Changelog format), `docs/postmortem-template.md`,
  `docs/runbooks/README.md` index, `docs/diagrams/` with the two mermaid
  diagrams from ARCHITECTURE.md as `.mmd` files.
- Commit: `Add ADRs, changelog, post-mortem template, and diagram sources.`

---

## Phase 1 — Domain models, policies, and ingest

Goal: raw JSON becomes typed, hashed rows in DuckDB, with reference data.

### Step 1.1 — Domain models and money

- `domain/money.py`: parse strings like `"1467"`, `"1.234,56"`, `"-12.5"` to
  `Decimal` with two places; `brl()` formatter.
- `domain/models.py`: `PayerKind` (`CNPJ`, `CPF`, `OUTRO`), `Despesa` frozen
  dataclass mirroring the JSON fields with typed values plus `record_sha256`,
  `Deputado`, `Competencia(ano, mes)`. `Despesa.from_raw(dict)` normalizes:
  digits-only `cnpj_cpf`, `PayerKind` by length, dates to `date`, amounts via
  `money`, empty strings to `None`.
- `record_sha256` = sha256 of the raw record serialized with sorted keys.
- Tests: fixtures for a deputy row and a leadership row (empty `cpf`,
  `siglaUF = "NA"`), malformed amounts raise a domain error.
- Commit: `Add domain models for expenses and deputies with BRL parsing.`

### Step 1.2 — Policies as data

- `domain/policies.py`: frozen dataclasses `Cap(category_code, limit, valid_from)`,
  `CotaUF(uf, value, valid_from)`, `Adicional(role, value, valid_from)`;
  constants from `cota-parlamentar.md` with `valid_from = 2023-01-01`
  (document the assumption in a comment and in ADR 0008).
- Map each cap to the `numeroSubCota` codes found in the data; put the
  mapping in `policies.py` with a comment listing the `descricao` strings.
- Migration `0002_reference.sql`: tables `caps`, `cota_uf`, `adicionais`.
- `application/ingest.py` function `load_policies()` upserting policy rows.
- Tests: every UF in the markdown table is present; cap codes are unique.
- Commit: `Add caps, state quotas, and leadership add-ons as versioned policy data.`

### Step 1.3 — Streaming JSON source

- `adapters/files/json_source.py`: iterate records from `data/Ano-YYYY.json`
  without loading the whole file (`ijson` is allowed; add it to dependencies),
  computing the file sha256 in the same pass; yield `(raw_dict, record_sha256)`.
- Port `ExpenseSource` in `application/ports.py`.
- Tests on a fixture file with 5 records; file hash matches `hashlib` on the bytes.
- Commit: `Add a streaming, hashing JSON source for yearly expense files.`

### Step 1.4 — Ingest use case

- Migration `0003_despesas.sql`: `despesas` (typed columns, `record_sha256`,
  `source_file_id`), `deputados` (derived; `lideranca` flag), indexes on
  `(numero_deputado_id, ano, mes)`, `(cnpj_cpf_digits)`, `(id_documento)`.
- `application/ingest.py`: for each year in settings, skip when
  `source_files.sha256` unchanged, otherwise insert rows in batches of 10k
  inside a step per file, then rebuild `deputados`. Emit counters.
- CLI `ingest [--anos 2023-2026] [--force]`.
- Fixture `tests/fixtures/mini_dataset/Ano-2099.json` (~200 synthetic records
  designed to trigger every rule in Phase 2 at least once, and leadership rows).
- Integration test: ingest the fixture into a temp db; re-run skips; `--force` reloads.
- Runbook `docs/runbooks/ingest.md`.
- Commit: `Add the ingest command loading yearly JSON into DuckDB with hashing.`

---

## Phase 2 — Rule engine and deterministic rules

Goal: rules produce versioned findings with provenance, idempotently.

### Step 2.1 — Rule protocol, registry, and findings schema

- `domain/rules/base.py`: `RuleDescriptor(id, version, title, severity,
  fundamento, needs: tuple[str, ...])`, `RuleResult(id_documento, amount,
  evidence: Mapping)`, `Rule` protocol with `descriptor`, `sql`, `evaluate(row)`.
- `domain/rules/registry.py`: `RULES: tuple[type[Rule], ...]` (empty for now)
  and `get_rule(id)`.
- Migration `0004_findings.sql`: `findings` with `finding_key`, `rule_id`,
  `rule_version`, `id_documento`, `severity`, `amount`, `evidence_json`,
  `status`, provenance columns (`run_id`, `step_id`, `source_file_id`,
  `record_sha256`, `code_commit`, `created_at`, `superseded_at`,
  `superseded_by_run_id`).
- `application/check.py`: for each selected rule: skip with recorded reason
  if a `needs` table is missing; run `sql`; evaluate rows; upsert by
  `finding_key`; mark previously active findings of the same rule and version
  that did not fire as `superseded`.
- Contract test: every registry rule has `.llms/rules/<id>-*.md` with
  frontmatter `id`, `version`, `severidade`, `fundamento`, and versions match
  (test passes trivially while the registry is empty, but the loader exists).
- CLI `check [--regras R01,R02] [--dry-run]`.
- Commit: `Add the rule protocol, registry, findings schema, and check command.`

### Step 2.2 — Monthly cap rules R01–R06

- One module per rule in `domain/rules/`: SQL sums `valor_liquido` per
  `(numero_deputado_id, ano, mes)` for the cap's category codes joined to
  `caps` by `valid_from`; evaluator emits one finding per month over the cap
  with `evidence = {total, limite, documentos: [...]}` attached to the latest
  `id_documento` of that month.
- `.llms/rules/R01-locacao-veiculos.md` … `R06-auxilio-moradia.md` in
  Portuguese: id, version, severidade, fundamento (quoted from
  `cota-parlamentar.md`), campos, lógica, exemplos, o que o agente deve verificar.
- Register in `registry.py`. Unit tests per rule on the mini dataset: one
  positive month, one negative month, boundary equal to cap does not fire.
- Commit: `Add monthly cap rules R01-R06 with agent-facing rule documents.`

### Step 2.3 — Quota ceiling R07 and leadership R14

- R07: cumulative in-year spend per deputy vs `cota_uf` with carryover
  (available(month) = quota × month − spent through previous month), plus
  `adicionais` when `deputados.lideranca` or role data says so; fire only when
  exceeded by `settings.rules.quota_margin_percent` (default 5). Evidence
  includes the series by month.
- R14: same computation for leadership rows using the leadership add-on;
  implemented as a parameterization of R07's SQL, not a copy.
- Add `rules.quota_margin_percent` to settings; add `.llms/rules/R07`, `R14`.
- Tests: carryover makes a high month legal; margin suppresses small overruns.
- Commit: `Add state quota ceiling rule R07 with carryover and leadership variant R14.`

### Step 2.4 — Payee, duplicate, and deadline rules R08–R10

- R08: `payer_kind = CPF` and category not in the allowed set (rent of
  property, aircraft/boat charter, taxi); evidence shows category and payee digits.
- R09: groups by `(cnpj_cpf_digits, numero, valor_documento)` with more than
  one `id_documento`; one finding per document in the group with the group listed.
- R10: `date(ano, mes, 1)` minus `data_emissao` > 90 days; evidence shows both.
- `.llms/rules/R08`–`R10`; tests per rule.
- Commit: `Add payee, duplicate invoice, and filing deadline rules R08-R10.`

### Step 2.5 — Check runbook and report command (first version)

- `application/report.py`: `report findings` writes
  `runs/<run_id>/findings.csv` and a markdown summary per rule (counts, totals).
- Runbooks `check.md`, `report.md`.
- Commit: `Add findings export and runbooks for check and report.`

---

## Phase 3 — External snapshots and dependent rules

Goal: TSE, Receita, and PDFs captured as replayable snapshots; R11–R13.

### Step 3.1 — Snapshot store

- `adapters/external/snapshots.py`: `Snapshot(source, snapshot_id, path,
  manifest)`; download with `httpx` streaming to
  `data/external/<source>/<snapshot_id>/`, write `manifest.json` (URL,
  status, headers subset, sha256, size, fetched_at, run_id); list and load
  latest snapshot. Port `SnapshotStore`.
- Migration `0005_snapshots.sql`: `snapshots` table.
- Tests with a local HTTP server fixture.
- Commit: `Add a replayable snapshot store for external downloads.`

### Step 3.2 — TSE candidates and R11

- `adapters/external/tse.py`: load candidate CSVs (2024 municipal, 2026
  general) from a snapshot into `tse_candidatos` (`snapshot_id`, year, cpf,
  nome normalizado, uf, cargo). Normalization helper shared in `domain/text.py`.
- `fetch tse` CLI; election dates in `policies.py` (`2024-10-06`, `2026-10-04`).
- R11: category "DIVULGAÇÃO DA ATIVIDADE PARLAMENTAR" with `data_emissao` in
  the 120 days before an election and deputy matched by CPF, else by
  normalized name + UF (evidence records which match was used). `needs =
  ("tse_candidatos",)`.
- Tests with a small CSV fixture; match by CPF and by name.
- Commit: `Add TSE candidate snapshots and the election blackout rule R11.`

### Step 3.3 — Receita CNPJ filtered load and R12–R13

- `adapters/external/cnpj_bulk.py`: given the Receita dump files in a
  snapshot, stream `Empresas` and `Socios` and keep only CNPJ roots present in
  `despesas`; tables `cnpj_empresas` (razão social, natureza jurídica, CNAE
  principal) and `cnpj_socios` (nome, qualificação). Memory-bounded; progress
  events every 1M lines.
- `fetch cnpj --from <dir>` (dump download stays manual; runbook explains).
- R12: a partner shares a surname token (length ≥ 4, not in a stoplist of
  common surnames kept in `.llms/rules/R12`) with the deputy; severity
  `revisar`; `needs = ("cnpj_socios",)`.
- R13: supplier CNAE or razão social incompatible with category (keyword and
  CNAE lists in `policies.py`); `needs = ("cnpj_empresas",)`.
- Tests with tiny dump fixtures.
- Commit: `Add filtered Receita CNPJ loading and supplier rules R12-R13.`

### Step 3.4 — PDF fetch and text extraction

- `adapters/external/pdf.py`: download `urlDocumento` to
  `data/external/pdf/<id_documento>.pdf` (cache by existence + sha), extract
  text with `pypdf`, record extractor version; `needs_ocr` when text is empty.
- Migration `0006_nf_texto.sql`; `fetch pdf --sample` pulls PDFs for the
  sampled findings of the latest `check` run (sampling logic in
  `application/sampling.py`, reused by Phase 4).
- Tests with a generated PDF fixture.
- Commit: `Add PDF snapshots with text extraction for sampled findings.`

---

## Phase 4 — Agents via AgentRig

Goal: Codex annotates sampled findings with structured pareceres, fully traced,
never deciding rule outcomes.

### Step 4.1 — Agent contracts, codecs, and prompt loading

- `adapters/agents/contracts.py`: `ParecerRequest` / `Parecer` dataclasses
  and strict codecs; `NotaFiscalRequest` / `NotaFiscalLeitura`;
  `ResumoDeputadoRequest` / `ResumoDeputado`. JSON schemas with
  `additionalProperties: false`.
- Prompts in `.llms/tasks/explicar-achado.md`, `revisar-nota.md`,
  `consolidar-deputado.md` with frontmatter `prompt_version`; loader returns
  text, version, sha256.
- Contracts: `READ_ONLY`, `network: denied`, `max_turns=1`,
  `max_tool_calls=0`, `allowed_capabilities` injected (scripted or Codex).
- Tests: codecs reject extra keys and invalid enums; prompt loader hashes.
- Commit: `Add agent contracts, strict codecs, and versioned prompt loading.`

### Step 4.2 — Runtimes and private trace recorder

- `adapters/agents/runtimes.py`: `build_codex_runtime(settings)` (read-only
  sandbox at repo root, ephemeral) and `build_scripted_runtime(scenarios)`.
- `adapters/agents/trace.py`: implement AgentRig `PrivateTraceRecorder`
  writing to `agent_traces` (migration `0007_agents.sql`: `agent_traces`,
  `pareceres`) with model, prompt version and hash, input, output, usage,
  duration, status, `run_id`, `finding_key`.
- Bridge AgentRig events into the run ledger `events` table via an `EventSink`.
- Contract tests with scripted runtime: a trace row and ledger events exist
  after one call.
- Commit: `Add Codex and scripted agent runtimes with full private tracing.`

### Step 4.3 — Explain use case and budget

- `application/explain.py`: select sample per rule (`sampling.per_rule`),
  skip `(finding_key, prompt_version, model)` already explained unless
  `--force`, attach `nf_texto` when present, call the per-rule explainer
  agent, then the invoice reader when text exists; store `pareceres`; stop
  when `agents.max_calls_per_run` is reached and record `budget.exhausted`.
- CLI `explain [--regras] [--amostra N] [--force]`; refuses live without
  `AGENTRIG_RUN_LIVE=1`, otherwise uses the scripted runtime with a fixture
  scenario set and says so.
- Integration test with scripted runtime on the mini dataset.
- Runbook `explain.md` including cost notes.
- Commit: `Add the explain command annotating sampled findings with agent pareceres.`

### Step 4.4 — Per-deputy reviewer agent

- `consolidar-deputado` agent over active findings with pareceres for one
  deputy; output stored in `resumos_deputado` (migration `0008`).
- CLI `explain --consolidar [--deputado ID]`.
- Commit: `Add the per-deputy reviewer agent and consolidated summaries.`

---

## Phase 5 — Review web app

Goal: a local reviewer approves or rejects findings; every decision is a run.

### Step 5.1 — Decisions and review use case

- Migration `0009_decisions.sql`: append-only `decisions`.
- `application/review.py`: `list_pending(filters)`, `get_finding(key)`,
  `decide(key, decision, comment)` — each `decide` is a run with one step.
  View `findings_current` joining the latest decision.
- Tests: two decisions on one finding keep both rows; current view shows the latest.
- Commit: `Add append-only decisions and the review use case.`

### Step 5.2 — FastAPI app

- `interfaces/web/app.py`, routes for list (filters: rule, deputy, UF,
  min amount), detail (record, rule text from `.llms/rules`, evidence,
  pareceres, PDF link, extracted text), approve/reject with comment via
  HTMX partials; Jinja2 templates in Portuguese; no JS build.
- CLI `review [--port 8765]` starting uvicorn.
- Tests with FastAPI test client: list renders, decide creates a decision and a run.
- Runbook `review.md`.
- Commit: `Add the review web app for approving findings.`

---

## Phase 6 — Reports, run reports, and documentation

### Step 6.1 — Final report

- `report final` exports only `aprovado` findings: CSV, per-deputy markdown
  (PT), and a summary with rule totals; includes provenance columns and
  parecer summaries.
- Commit: `Add the final report over approved findings.`

### Step 6.2 — Run reports

- On every run finish, render `runs/<run_id>/report.md` from the ledger:
  command, duration, steps with status, counters, failures with messages,
  produced artifacts. `runs show` links to it.
- Commit: `Generate a markdown report for every run from the ledger.`

### Step 6.3 — Documentation pass

- Fill `docs/runbooks/` for every command; `docs/CHANGELOG.md` entries for
  all rule, schema, and prompt versions so far; ADR 0008 (policy
  `valid_from` assumption) and any new decisions; `opsiu/README.md` with
  quick start.
- Commit: `Complete runbooks, changelog, and README for the first release.`

---

## Phase 7 — Evals and CI

### Step 7.1 — Labeled set and graders

- `evals/labeled_findings.jsonl` schema (finding_key, expected
  recommendation, notes) with 50 placeholders filled from the mini dataset;
  `evals/graders.py` using AgentRig graders to score recommendation
  agreement; `pytest -m eval` runs with scripted runtime by default and Codex
  with `AGENTRIG_RUN_LIVE=1`.
- Commit: `Add a labeled eval set and graders for agent recommendations.`

### Step 7.2 — GitHub Actions

- Workflow running `uv sync`, ruff, mypy, `pytest -m "not eval"` on push and
  PR; caches uv.
- Commit: `Add CI running lint, type checks, and offline tests.`

---

## Parallelism

- Phase 0 steps are sequential.
- After 1.4: Steps 2.1 and 3.1 can run in parallel; 2.2–2.5 depend on 2.1;
  3.2–3.4 depend on 3.1 and 2.1.
- Phase 4 depends on 2.1 and 3.4; Phase 5 on 2.1; Phase 6 on 4 and 5;
  Phase 7 on 4.

## Definition of done per phase

- Phase 0: `db migrate`, a dummy run shows in `runs list`, manifest written.
- Phase 1: `ingest` on the mini dataset; re-run is a no-op.
- Phase 2: `check` yields the expected findings on the mini dataset; re-run
  produces no duplicates.
- Phase 3: `fetch tse|cnpj|pdf` replay from snapshots; R11–R13 fire on fixtures.
- Phase 4: `explain` with scripted runtime writes pareceres and traces; one
  live Codex call succeeds end to end.
- Phase 5: a decision made in the browser appears as a run and a decision row.
- Phase 6: `report final` contains only approved findings; every command has a runbook.
- Phase 7: CI green; eval score reported.
