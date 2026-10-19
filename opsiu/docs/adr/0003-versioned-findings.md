# ADR 0003: Version findings and retain human decisions

Status: Accepted. Findings and human decisions were implemented in Phases 2 and 5;
approved-only final exports were implemented in Step 6.1.

## Context

Rules and source data can change between audits. Re-running a check must not create
duplicate findings or erase the evidence behind an earlier review.

## Decision

Identify a finding by `(rule_id, rule_version, id_documento)`. Deterministic rules
produce findings; agents can annotate them but cannot decide that a rule was
broken. Bump the rule version whenever its SQL or evaluator changes, keep the
agent-facing rule document in sync, and explain the change in the changelog.

Re-evaluate findings by their stable key. Mark findings that no longer apply, or
that belong to replaced rule versions, as `superseded` instead of deleting them.
Retain provenance linking each finding to the run, step, source and record hashes,
rule version, and code commit.

Append every human decision with reviewer, comment, and timestamp. The latest
decision is current; previous decisions remain available. Final reports include
only findings explicitly approved by the reviewer.

[ADR 0009](0009-immutable-finding-conflicts.md) specifies how changed evidence and
recurring identities fail without rewriting history.
[ADR 0010](0010-append-only-finding-lifecycle.md) records the later storage
refinement: immutable finding records, appended supersession events, and the
compatible `findings` read view.

## Consequences

Repeated checks remain idempotent, and reviewers can distinguish current results
from historical versions. Queries must deliberately select active findings and
the latest decision. Retained findings and decisions increase storage, but preserve
the basis for later corrections and investigation.

Source: [core types and rule protocol](../../ARCHITECTURE.md#domain-core-types).
