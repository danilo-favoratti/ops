# ADR 0012: Preserve accounting occurrences within document findings

## Status

Accepted.

## Context

The real CEAP sources contain multiple accounting rows for one `idDocumento`.
Rows can differ in net amount, invoice gross amount, or competence. Treating a
published document ID as a unique source-row key aborted R01 and R09. R11 also
aborted an entire election check when one name/UF matched several candidate
identities with unavailable CPFs.

## Decision

R01, R09, and R11 version 2 explicitly enable document-row grouping. Keep the
immutable finding key `(rule_id, rule_version, id_documento)` and retain every
evaluated occurrence, sorted by canonical evidence and amount. When multiple
occurrences or source records exist, include all distinct current source-file
and record hashes for that document. These hashes describe document context;
not every accounting row necessarily triggered the rule. Unchanged single-row
cases retain their existing evidence structure, apart from R11's ambiguity flag.

Primary provenance is the first source ordered by file path and record hash.
It is an anchor for existing exports/review APIs, not the sole evidence for the
combined finding. The expense repository retains every original row and its
source position. Agent explanations must consider the finding evidence, not
infer the total from the single anchor expense. No expense is deleted or merged.

The finding amount sums evaluated occurrence amounts: monthly excesses for R01,
distinct duplicate-group gross amounts for R09, and in-window accounting-row net
amounts for R11. This is not an assertion of recoverable loss. R09 still uses
its existing distinct document/group SQL; repeated copies of a document within
one duplicate group do not multiply the gross amount.

R11 keeps all candidate identities and emits a `revisar` indicator for an
ambiguous name/UF match. Exact CPF precedence and the election window remain
unchanged. Other rules retain their strict single-origin/unique-result behavior.
Missing external imports and invalid data still fail rather than become no-match.

## Consequences and verification

Old findings and review history are immutable. New rule versions avoid changing
existing finding evidence under an old key. Synthetic eval labels are re-keyed
for R01/R09 without changing expected recommendations or fixture contents.

Regression tests cover repeated accounting rows in the actual R01/R09 pipeline,
combined amounts and source hashes, unchanged reruns, forced identical ingestion,
exports, and ambiguous R11 matches. Publication validates that the anchor is an
actual current source record. Source hashes and occurrence ordering make retries
stable without relying on newly generated source IDs.
