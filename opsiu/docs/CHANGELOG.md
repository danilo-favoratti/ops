# Changelog

All notable rule, schema, and prompt version changes are documented here.
This file follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

## [Unreleased]

### Added

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
