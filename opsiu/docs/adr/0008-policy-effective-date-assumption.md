# ADR 0008: An explicit effective-date assumption for policy values

Status: Accepted, with a documented historical-data limitation.

## Context

The product plan audits 2023–2026 using the amounts recorded in
[`cota-parlamentar.md`](../../cota-parlamentar.md). That source describes current
caps, state quotas, and leadership additions; it does not provide a historical
schedule of changes for every audited year. Silently presenting those amounts as
verified historical policy would overstate the evidence.

## Decision

Store the supplied amounts as exact `Decimal` values in frozen policy records.
Assign the initial records `valid_from = 2023-01-01`, as required by the product
and implementation plans. This date is an implementation assumption, not a claim
that every amount took legal effect on that day.

Preserve effective dates in the `caps`, `cota_uf`, and `adicionais` tables. Policy
identity includes the category, state, or role and its effective date. Map caps
to the source dataset's `numeroSubCota` codes and document their descriptions in
code. Load policies through application ports inside a run.

Use the published monetary course/event cap of R$ 7.697,17 from the supplied
source. Its accompanying percentage and the supplied current lowest state quota
do not reconcile; do not silently recalculate or replace the stated cap.

Represent the named monthly leadership roles separately. Do not turn the Mercosul
allowance into a monthly leadership addition: the source ties it to each official
trip, and the initial audit has no corresponding trip-entitlement evidence.

## Consequences

Policy values are reproducible, inspectable, and ready for a future researched
schedule. Rules can select the policy effective for an expense period without
hard-coding amounts in their SQL. The initial audit retains a historical-policy
limitation until dated evidence is added.

Quota checks must retain the product plan's configurable margin and approximate
interpretation. Human review remains necessary before final reporting. New
effective dates must preserve earlier policy rows; corrected rule or schema
versions must be documented in the changelog as required by the architecture.
