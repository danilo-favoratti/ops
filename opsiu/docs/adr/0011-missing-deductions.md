# ADR 0011: Preserve missing deductions as unknown

## Status

Accepted.

## Context

The first production ingestion of 2023–2026 failed because some records have
`valorGlosa: null`. The synthetic fixture contained only explicit deductions.
The failing examples pair a positive face amount with a negative net amount.
Replacing the missing deduction with zero or calculating it from those amounts
would introduce a fact the source did not provide.

The [Câmara field documentation](https://dadosabertos.camara.leg.br/howtouse/2023-12-26-dados-ceap.html)
defines a deduction as the amount not covered by CEAP, documents negative
accounting values and multiple records for one document, but does not state
that a null deduction must be interpreted as zero. We therefore retain unknowns
without inferring their accounting meaning.

## Decision

Normalize null, absent, or blank `valorGlosa` as `None`; preserve explicit zero
as `Decimal("0.00")`. Keep strict parsing for any supplied deduction and retain
required gross/net amounts and document identity. Preserve original record
hashing before normalization and never modify source files.

Migration `0010_nullable_glosa` rebuilds the expense table transactionally with
the same columns, keys, foreign keys, and indexes, changing only deduction
nullability. It restores the unchanged current-source view. A populated upgrade
and deliberately failed migration verify preservation and rollback. Direct
alteration after removing indexes was rejected because it failed DuckDB's
populated rollback test; previous committed migrations remain untouched.

Exports leave the CSV deduction blank and explicitly render `não informada`
in final Markdown. The review UI already renders null as `Não informado`.
Agent request schemas advance to v2 and the two affected prompts advance to
version 2, preserving null rather than converting it to a decimal string.
Existing successful annotations remain recorded under their original versions.

## Consequences

Deterministic rules continue to use their existing source amounts. No rule
version changes: no rule predicate or evidence construction uses `valor_glosa`.
This fix does not fill missing document IDs, quarantine records, or publish
partial yearly snapshots. Those remain validation failures under the current
ingestion contract. The full streaming precheck also found six missing document
IDs in 2025–2026; their handling requires a separate identity-policy decision.
