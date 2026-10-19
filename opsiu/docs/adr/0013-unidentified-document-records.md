# ADR 0013: Source-record identity for unidentified documents

## Status

Accepted, explicitly authorized after discovering unrelated CEAP rows with ID zero.

## Decision

Keep every existing finding key and all source IDs unchanged. Positive document
IDs retain `(rule_id, rule_version, id_documento)` identity. An opt-in rule may
extend the key for nonpositive IDs with `record_identity`, the SHA-256 of the
canonical JSON array `["ceap", source_year, record_sha256]`. The annual source
year identifies the logical CEAP file; neither its local path, byte hash, row
position nor generated import UUID belongs to the identity.

Distinct records have separate findings and exact source attribution. Identical
copies in the same annual source share identity; occurrence evidence and summed
matching net amounts retain their multiplicity. Changed source content creates a
new identity; successful publication supersedes absent old findings without
rewriting them. Existing immutable-conflict and supersession rules still apply.

R12 version 2 opts in and also groups accounting occurrences for positive IDs.
Other rule versions and historical zero-ID findings retain their old identities.
A missing input ID rejected during ingestion is not silently normalized or invented;
this change handles the nonpositive IDs already represented in accepted records.

Migration 0011 extends the database uniqueness constraint with a default-empty
discriminator. It transactionally rebuilds referenced relations and preserves
all finding keys, lifecycle events, decisions, PDF links, traces, opinions and
summary references. Reader defaults support legacy records. The review UI says
“Documento sem identificador” while audit evidence retains the original value.

## Verification

Test populated migration and rollback, foreign-key enforcement, distinct deputies
sharing zero, source-year separation, exact adjustment totals, correct review and
export attribution, scripted annotation keys, unchanged and forced-ingestion replay.
No agent may infer a valid invoice number or confirmed relationship from these keys.
