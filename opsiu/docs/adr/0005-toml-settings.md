# ADR 0005: Typed TOML settings with environment overrides

Status: Accepted.

## Context

Paths, audit years, sampling limits, agent budgets, and reviewer identity vary
between local runs and tests. Audit manifests need a stable identity for the
effective configuration.

## Decision

Use pydantic-settings to load typed sections from `opsiu.toml` in the current
working directory, or the file selected by `OPSIU_CONFIG`. Environment variables
use the `OPSIU_` prefix and `__` for nested fields. Explicit model values take
precedence over environment values, which override TOML and then built-in defaults.

Validate values and reject unknown keys. Keep settings immutable and resolve
relative paths against the working directory. Compute `settings_hash()` as the
SHA-256 of canonical JSON containing the resolved settings, and include it in every
run manifest. This identifies effective values, independent of TOML formatting or
key order.

## Consequences

Tests can isolate all paths and budgets without modifying the shipped defaults.
Environment overrides remain visible through the effective settings hash. The
hash alone cannot reconstruct values, so reproducing a run also requires its
configuration and relevant environment overrides. Invalid settings abort a run
with a recorded configuration error.

Source: [configuration](../../ARCHITECTURE.md#configuration).
