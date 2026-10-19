# Changelog

All notable rule, schema, and prompt version changes are documented here.
This file follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

## [Unreleased]

### Added

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
