# Changelog

All notable rule, schema, and prompt version changes are documented here.
This file follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

## [Unreleased]

### Fixed

- Schema migration `0007a_findings_lifecycle` separates immutable finding records
  from append-only supersession events. The `findings` view preserves the
  existing read schema and current status. Existing findings and supersession
  history are retained; PDF and agent foreign keys are retargeted transactionally
  without changing any audit record or ID. This fixes DuckDB rejecting
  supersession updates to indexed status and foreign-key columns after audit
  references exist. Existing migrations and rule versions remain unchanged.

### Added

- Schema migration `0008_resumos_deputado`: immutable per-deputy summaries and
  exact source links to findings, opinions, private traces, and original source
  hashes. Summary identity includes the complete input hash, model, and prompt
  version; repeated inputs are reused unless explicitly forced.
- `explain --consolidar [--deputado ID]` summarizes active findings with existing
  successful opinions from the selected model. Scripted mode is explicit, calls
  share the per-run summary budget, and strict validation preserves every input
  finding and its deterministic severity. Summaries never approve findings.
- Schema migration `0007_agents`: append-only terminal agent traces and
  parecer records with finding, run, step, model, prompt version/hash, schema,
  input/output, usage, duration, and status provenance. Separate private trace
  records persist captured content before terminal completion, retaining
  evidence when an invocation fails or is interrupted.
- Scripted and Codex runtime factories, with live execution gated by
  `AGENTRIG_RUN_LIVE=1` and the read-only, network-denied agent contract.
  AgentRig events join the originating run through a safe metadata bridge;
  prompts, invoice text, responses, and other private content remain in private
  trace storage. Terminal tracing includes strict codec failures. The `explain`
  command and its orchestration remain scheduled for the next step.
- Prompt version `1` for `explicar-achado`, `revisar-nota`, and
  `consolidar-deputado`: Portuguese instructions require strict structured
  annotations from supplied evidence, treat source text as untrusted data,
  forbid tools/network use, and reserve rule decisions and approval for their
  deterministic and human owners. Prompts preserve uncertainty, overlapping
  amounts, surname-matching limitations, and null invoice facts when unknown.
- Schema migration `0006_nf_texto`: immutable PDF text extractions with raw
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
- Schema migration `0005b_cnpj`: immutable local Receita snapshots, filtered
  import history, covered supplier roots, companies, individual/other partners,
  and establishments, with current views and source/run/step provenance.
  Loading retains only roots present in current expenses. Unchanged content and
  root coverage reuse the current import; expanded roots can reuse the original
  raw snapshot without replacing its identity or previously published history.
- Rules `R12` and `R13`, version `1`, with `revisar` severity. R12 flags eligible
  shared surname tokens between a deputy and an individual company partner,
  with an explicit common-surname and generational stoplist. R13 checks narrow
  grocery and driver-service indicators against categories `13` and `120`,
  using Receita legal names and the exact establishment's principal CNAE.
  Both are review heuristics: neither proves kinship, historical ownership,
  nor the goods or services actually invoiced.
- Schema migration `0005a_tse`: immutable TSE candidate and import history with
  snapshot, source-member/record, run, and step provenance, plus current views
  selecting the latest successful import per election year. TSE loading keeps
  the policy election date, first round, and ordinary election type; unchanged
  current hashes retain their original import, and failed loads preserve it.
- Rule `R11`, version `1`, with high severity: publicity expenses in category
  `5` issued during the 120 days before the 2024 or 2026 policy election date.
  The lower bound is included and election day excluded. Matching prioritizes
  CPF; exact normalized name plus UF is allowed only with a missing CPF and
  cannot override conflicting complete CPFs. Ambiguous candidate identities
  fail the rule. Portuguese evidence links the candidate and snapshot used.
- Schema migration `0005_snapshots`: immutable external payload metadata with
  source, URL, safe response headers, SHA-256, byte size, fetch time, and run/step
  provenance. Streaming downloads publish only complete successful responses;
  local replay checks the manifest and payload against the indexed snapshot.
- Rules `R08`–`R10`, version `1`: payments to CPF outside category exceptions
  (`R08`, high severity), potential duplicate invoices sharing payee, exact
  number, and gross amount (`R09`, medium severity), and issue-to-competence
  intervals strictly over 90 days (`R10`, medium severity). Portuguese rule
  documents describe the broad category exceptions, the lack of a direct
  duplicate-invoice clause in the supplied source, overlapping duplicate
  amounts, and the absence of actual submission dates in the monthly proxy.
- Rules `R07` and `R14`, version `1`, with medium severity and explicit
  approximate evidence: shared cumulative annual quota calculation includes
  zero-spend months, dated monthly policies, and no carryover between years.
  The finite nonnegative `rules.quota_margin_percent` setting defaults to 5;
  thresholds remain unrounded and equality does not trigger. Finding amounts
  retain the full excess above the base allowance and overlap across months.
  R07 uses state quotas for regular deputies and documents missing individual
  role history; R14 uses one explicitly resolved leadership addition with zero
  state-quota base. Unknown leadership roles or missing dated policies fail
  before finding publication. Portuguese rule documents explain these limits.
- Rules `R01`–`R06`, version `1`: monthly caps for vehicle rental (category
  `120`), taxi/tolls/parking (`122`), fuel/lubricants (`3`), security (`8`),
  courses/events (`137`), and housing supplementation (`40`). Each sums current
  net expenses by deputy and month, selects the latest applicable dated cap,
  and records only the excess above the limit with the grouped document list.
  Missing effective policies fail the rule. Portuguese rule documents quote
  the published basis and specify positive, negative, and equal-limit cases;
  R05 retains the published BRL 7,697.17 amount under ADR 0008.
- Schema migration `0004_findings`: unique versioned finding identities, exact
  amounts, canonical evidence, source/run/step/code provenance, and explicit
  supersession metadata. Repeated matching findings retain their original rows;
  conflicting evidence or superseded-key recurrence fails atomically (ADR 0009).
- Schema migration `0003_despesas`: typed expense snapshots with immutable source,
  record, run and step provenance; yearly source metadata; indexed expense lookup;
  the `despesas_current` view; and the derived current deputy table. Reloads retain
  published history and atomically replace each year's current snapshot.
- Schema migration `0002_reference`: dated category caps, state quotas, and
  monthly role additions with the originating run and step. Equal repeated
  values are unchanged; conflicting values for the same key/date are rejected.
  New effective dates preserve existing policy versions.
- Policy data from `cota-parlamentar.md`, provisionally effective from
  `2023-01-01` as documented in ADR 0008.

- Schema migration `0001_init`: ordered migration history, runs, steps,
  events, and source file provenance. Migration scripts are immutable after commit.
