# Changelog

All notable rule, schema, and prompt version changes are documented here.
This file follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

## [Unreleased]

### Fixed

- TSE imports recognize the official non-disclosable CPF sentinel `-4` as
  unknown, preserving candidates and snapshot provenance. Existing R11 name/UF
  fallback remains unchanged; malformed CPF values still fail. Updated the
  Receita CNPJ catalog URL to the official shared-folder interface.

- Missing `valorGlosa` is preserved as unknown throughout ingestion, exports,
  human review, and agent inputs. Zero remains distinct; gross and net amounts,
  including negative values, and original source/record hashes are unchanged.
  Invalid supplied deductions and missing required fields still fail the yearly
  transaction. Normalization failures now identify their source record number.
- Migration [`0010_nullable_glosa`](../src/opsiu/adapters/duckdb/migrations/0010_nullable_glosa.sql)
  transactionally rebuilds `despesas` with nullable `valor_glosa`, retaining all
  rows, constraints, indexes, and the current-source view. Populated upgrade and
  rollback tests cover existing findings and private agent history.
- Prompt versions `2` for [explicar-achado](../.llms/tasks/explicar-achado.md)
  and [revisar-nota](../.llms/tasks/revisar-nota.md), with input schemas
  `opsiu.parecer-request.v2` and `opsiu.nota-fiscal-request.v2`, explicitly retain
  null deductions and prohibit inference from gross/net differences. Outputs
  and deterministic rule versions are unchanged; prior annotations remain
  immutable. See [ADR 0011](adr/0011-missing-deductions.md).
- Schema migration [`0007a_findings_lifecycle`](../src/opsiu/adapters/duckdb/migrations/0007a_findings_lifecycle.sql)
  separates immutable finding records
  from append-only supersession events. The `findings` view preserves the
  existing read schema and current status. Existing findings and supersession
  history are retained; PDF and agent foreign keys are retargeted transactionally
  without changing any audit record or ID. This fixes DuckDB rejecting
  supersession updates to indexed status and foreign-key columns after audit
  references exist. Existing migrations and rule versions remain unchanged.
  [ADR 0010](adr/0010-append-only-finding-lifecycle.md) records the lifecycle decision.

### Added

- [GitHub Actions CI](../../.github/workflows/opsiu.yml) on push and pull request:
  pinned actions and uv, Python 3.12, dependency caching, locked installation,
  Ruff lint and format checks, strict mypy, and the offline test suite. CI does
  not require production data or Codex credentials and disables the live gate.
- A 50-case synthetic recommendation evaluation set and native AgentRig
  agreement grader. Evaluations use isolated, audited runs, bounded sampling,
  private traces, and separate agreement and valid-output coverage metrics.
  Scripted mode is the default; labels are never supplied to the agent.
  [Dataset assumptions and usage](../evals/README.md) distinguish the constant
  scripted baseline from a live model evaluation. No rule, schema, or prompt
  version changes are introduced by this evaluation harness.
- Automatic Portuguese `report.md` for every finalized run, including failed and
  aborted runs. Reports summarize public ledger metadata, steps, durations,
  counters, failures, and artifact links without reading private agent content.
  Exclusive creation preserves existing reports and the manifest survives a
  report-rendering failure. `runs show` exposes the path.
- `report final` exports only active findings whose latest human decision is
  approved: `final.csv`, `final.md`, and one `deputado-{ID}.md` per deputy.
  Exports retain original expense provenance, the approving decision, and
  successful opinion history. Completed artifacts remain immutable and recorded
  when a later export fails; amounts retain the rules' overlap limitations.
- Localhost `review` web application with filters, original rule and expense
  evidence, verified cached PDFs, agent annotations, and chronological human
  decisions. Each decision has its own run; `revisar` remains pending, and agents
  cannot approve findings.
- Schema migration [`0009_decisions`](../src/opsiu/adapters/duckdb/migrations/0009_decisions.sql):
  append-only human decisions with reviewer,
  comment, decision time, immutable finding identity, and run/step provenance.
  The `findings_current` view joins the latest decision without discarding
  earlier decisions. Each decision action has its own run and step; superseded
  findings retain their history and cannot receive new decisions.
- Schema migration [`0008_resumos_deputado`](../src/opsiu/adapters/duckdb/migrations/0008_resumos_deputado.sql):
  immutable per-deputy summaries and
  exact source links to findings, opinions, private traces, and original source
  hashes. Summary identity includes the complete input hash, model, and prompt
  version; repeated inputs are reused unless explicitly forced.
- `explain --consolidar [--deputado ID]` summarizes active findings with existing
  successful opinions from the selected model. Scripted mode is explicit, calls
  share the per-run summary budget, and strict validation preserves every input
  finding and its deterministic severity. Summaries never approve findings.
- `explain` annotates bounded samples from successful rule checks in the latest
  eligible check run. It reuses successful opinions and invoice readings unless
  forced, applies the per-run call budget to all attempts, and records isolated
  failures. Cached invoice text is optional; the command never downloads PDFs.
  Scripted runs are explicitly simulated and do not establish audit conclusions.
- Scripted scenario version `1` for
  [`annotations-v1.json`](../.llms/scenarios/annotations-v1.json) and
  [`consolidation-v1.json`](../.llms/scenarios/consolidation-v1.json).
  Fixture hashes and versions are recorded in the run manifest. These scenarios
  exercise offline orchestration and structured outputs; they are not labeled
  evaluation results or evidence that an agent's recommendation is correct.
- Schema migration [`0007_agents`](../src/opsiu/adapters/duckdb/migrations/0007_agents.sql):
  append-only terminal agent traces and
  parecer records with finding, run, step, model, prompt version/hash, schema,
  input/output, usage, duration, and status provenance. Separate private trace
  records persist captured content before terminal completion, retaining
  evidence when an invocation fails or is interrupted.
- Scripted and Codex runtime factories, with live execution gated by
  `AGENTRIG_RUN_LIVE=1` and the read-only, network-denied agent contract.
  AgentRig events join the originating run through a safe metadata bridge;
  prompts, invoice text, responses, and other private content remain in private
  trace storage. Terminal tracing includes strict codec failures.
- Prompt version `1` for
  [`explicar-achado`](../.llms/tasks/explicar-achado.md),
  [`revisar-nota`](../.llms/tasks/revisar-nota.md), and
  [`consolidar-deputado`](../.llms/tasks/consolidar-deputado.md):
  Portuguese instructions require strict structured
  annotations from supplied evidence, treat source text as untrusted data,
  forbid tools/network use, and reserve rule decisions and approval for their
  deterministic and human owners. Prompts preserve uncertainty, overlapping
  amounts, surname-matching limitations, and null invoice facts when unknown.
- Schema migration [`0006_nf_texto`](../src/opsiu/adapters/duckdb/migrations/0006_nf_texto.sql):
  immutable PDF text extractions with raw
  snapshot identity, decoded PDF path/hash/size, extractor name and version,
  text hash, page count, OCR-needed flag, and run/step provenance. The
  `nf_texto_findings` relation preserves links to each original finding and its
  expense origin without replacing earlier extraction rows.
- Sampled PDF capture and `pypdf` extraction through `fetch pdf --sample`.
  Sampling uses successful rule checks in the latest eligible completed check,
  retains original finding URLs, and bounds each rule by `sampling.per_rule`.
  Verified cache reuse avoids HTTP; explicit refresh creates a new raw snapshot
  and PDF version. Extractor upgrades append text extraction from verified
  cached bytes. Empty text is flagged for OCR; OCR itself is not implemented.
- Schema migration [`0005b_cnpj`](../src/opsiu/adapters/duckdb/migrations/0005b_cnpj.sql):
  immutable local Receita snapshots, filtered
  import history, covered supplier roots, companies, individual/other partners,
  and establishments, with current views and source/run/step provenance.
  Loading retains only roots present in current expenses. Unchanged content and
  root coverage reuse the current import; expanded roots can reuse the original
  raw snapshot without replacing its identity or previously published history.
- Rules [`R12`](../.llms/rules/R12-sobrenome-socio.md) and
  [`R13`](../.llms/rules/R13-fornecedor-incompativel.md), version `1`,
  with `revisar` severity. R12 flags eligible
  shared surname tokens between a deputy and an individual company partner,
  with an explicit common-surname and generational stoplist. R13 checks narrow
  grocery and driver-service indicators against categories `13` and `120`,
  using Receita legal names and the exact establishment's principal CNAE.
  Both are review heuristics: neither proves kinship, historical ownership,
  nor the goods or services actually invoiced.
- Schema migration [`0005a_tse`](../src/opsiu/adapters/duckdb/migrations/0005a_tse.sql):
  immutable TSE candidate and import history with
  snapshot, source-member/record, run, and step provenance, plus current views
  selecting the latest successful import per election year. TSE loading keeps
  the policy election date, first round, and ordinary election type; unchanged
  current hashes retain their original import, and failed loads preserve it.
- Rule [`R11`](../.llms/rules/R11-divulgacao-eleitoral.md), version `1`,
  with high severity: publicity expenses in category
  `5` issued during the 120 days before the 2024 or 2026 policy election date.
  The lower bound is included and election day excluded. Matching prioritizes
  CPF; exact normalized name plus UF is allowed only with a missing CPF and
  cannot override conflicting complete CPFs. Ambiguous candidate identities
  fail the rule. Portuguese evidence links the candidate and snapshot used.
- Schema migration [`0005_snapshots`](../src/opsiu/adapters/duckdb/migrations/0005_snapshots.sql):
  immutable external payload metadata with
  source, URL, safe response headers, SHA-256, byte size, fetch time, and run/step
  provenance. Streaming downloads publish only complete successful responses;
  local replay checks the manifest and payload against the indexed snapshot.
- Rules [`R08`](../.llms/rules/R08-pagamento-cpf.md),
  [`R09`](../.llms/rules/R09-nota-duplicada.md), and
  [`R10`](../.llms/rules/R10-prazo-reembolso.md), version `1`:
  payments to CPF outside category exceptions
  (`R08`, high severity), potential duplicate invoices sharing payee, exact
  number, and gross amount (`R09`, medium severity), and issue-to-competence
  intervals strictly over 90 days (`R10`, medium severity). Portuguese rule
  documents describe the broad category exceptions, the lack of a direct
  duplicate-invoice clause in the supplied source, overlapping duplicate
  amounts, and the absence of actual submission dates in the monthly proxy.
- Rules [`R07`](../.llms/rules/R07-cota-uf.md) and
  [`R14`](../.llms/rules/R14-liderancas.md), version `1`, with medium severity and explicit
  approximate evidence: shared cumulative annual quota calculation includes
  zero-spend months, dated monthly policies, and no carryover between years.
  The finite nonnegative `rules.quota_margin_percent` setting defaults to 5;
  thresholds remain unrounded and equality does not trigger. Finding amounts
  retain the full excess above the base allowance and overlap across months.
  R07 uses state quotas for regular deputies and documents missing individual
  role history; R14 uses one explicitly resolved leadership addition with zero
  state-quota base. Unknown leadership roles or missing dated policies fail
  before finding publication. Portuguese rule documents explain these limits.
- Rules [`R01`](../.llms/rules/R01-locacao-veiculos.md),
  [`R02`](../.llms/rules/R02-taxi-pedagio-estacionamento.md),
  [`R03`](../.llms/rules/R03-combustiveis-lubrificantes.md),
  [`R04`](../.llms/rules/R04-seguranca.md),
  [`R05`](../.llms/rules/R05-cursos-eventos.md), and
  [`R06`](../.llms/rules/R06-auxilio-moradia.md), version `1`:
  monthly caps for vehicle rental (category
  `120`), taxi/tolls/parking (`122`), fuel/lubricants (`3`), security (`8`),
  courses/events (`137`), and housing supplementation (`40`). Each sums current
  net expenses by deputy and month, selects the latest applicable dated cap,
  and records only the excess above the limit with the grouped document list.
  Missing effective policies fail the rule. Portuguese rule documents quote
  the published basis and specify positive, negative, and equal-limit cases;
  R05 retains the published BRL 7,697.17 amount under
  [ADR 0008](adr/0008-policy-effective-date-assumption.md).
- Schema migration [`0004_findings`](../src/opsiu/adapters/duckdb/migrations/0004_findings.sql):
  unique versioned finding identities, exact
  amounts, canonical evidence, source/run/step/code provenance, and explicit
  supersession metadata. Repeated matching findings retain their original rows;
  conflicting evidence or superseded-key recurrence fails atomically
  ([ADR 0009](adr/0009-immutable-finding-conflicts.md)).
- Schema migration [`0003_despesas`](../src/opsiu/adapters/duckdb/migrations/0003_despesas.sql):
  typed expense snapshots with immutable source,
  record, run and step provenance; yearly source metadata; indexed expense lookup;
  the `despesas_current` view; and the derived current deputy table. Reloads retain
  published history and atomically replace each year's current snapshot.
- Schema migration [`0002_reference`](../src/opsiu/adapters/duckdb/migrations/0002_reference.sql):
  dated category caps, state quotas, and
  monthly role additions with the originating run and step. Equal repeated
  values are unchanged; conflicting values for the same key/date are rejected.
  New effective dates preserve existing policy versions.
- Policy data from [`cota-parlamentar.md`](../cota-parlamentar.md), provisionally
  effective from `2023-01-01` as documented in
  [ADR 0008](adr/0008-policy-effective-date-assumption.md).
- Schema migration [`0001_init`](../src/opsiu/adapters/duckdb/migrations/0001_init.sql):
  ordered migration history, runs, steps,
  events, and source file provenance. Migration scripts are immutable after commit.
