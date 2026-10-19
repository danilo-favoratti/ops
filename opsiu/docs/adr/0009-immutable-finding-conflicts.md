# ADR 0009: Fail checks that conflict with immutable finding identities

Status: Accepted as the conservative implementation default; an explicit future
product decision is required to introduce source revisions into finding identity.

## Context

The architecture identifies a finding by `(rule_id, rule_version, id_documento)`
and retains its evidence, provenance, decisions, and agent traces. Refreshed source
data can produce different evidence for that same identity. Silently updating a
finding would change the evidence underlying an earlier review. Reactivating a
superseded row would also erase the meaning of its completed lifecycle.

## Decision

Use a stable SHA-256 of the canonical identity tuple as `finding_key`, with a
separate database uniqueness constraint on the three components. A repeated
active finding is unchanged only when severity, exact amount, canonical evidence,
and originating record hash match. Preserve its original run, step, timestamp,
source reference, and code commit. A forced ingestion of identical source content
therefore does not move an existing finding to the new source snapshot.

If any compared payload differs, or if the same key was already superseded, fail
the rule step explicitly. Roll back every insertion and supersession attempted by
that rule. Other selected rules may continue. Never rewrite the old evidence or
reactivate the old finding. Resolving such a conflict requires an explicit policy
or a deliberate new rule version; the checker never invents one automatically.

After a complete successful evaluation, mark active findings of that rule which
were not emitted as superseded, including older rule versions. Record when and
which run superseded them. Missing required relations, evaluation failures,
publication conflicts, and dry runs cannot supersede findings.

A finding's document ID must resolve to one distinct source origin in
`despesas_current`. Identical repeated rows within a source may share an origin;
different record hashes or source origins are ambiguous and fail the rule. Do not
choose an arbitrary latest row that may differ from the rule's evaluated record.

## Consequences

Retries preserve complete original finding rows. Source updates remain visible
without invalidating prior reviews. A recurring same-version finding or changed
same-key evidence currently requires operator intervention instead of a silent
history rewrite. Dry runs record candidate and finding counts without publishing
or altering findings; publication conflicts are checked only during publication.

Rule SQL uses `despesas_current` for current expenses. The repository accepts one
SELECT statement, rejects duplicate candidate column names, and closes the cursor
before publishing. Result publication is a transaction, with ledger events emitted
only after commit or rollback so database and file event histories agree.

Sources: [ADR 0003](0003-versioned-findings.md),
[architecture](../../ARCHITECTURE.md#domain-core-types).
