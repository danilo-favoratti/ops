# Changelog

All notable rule, schema, and prompt version changes are documented here.
This file follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

## [Unreleased]

### Added

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
