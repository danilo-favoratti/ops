# ADR 0014: Researched policy coverage and explicit rule suspension

Status: Accepted.

## Context

Review of real findings exposed recurring weaknesses: outdated rental and course
caps, treating invoice issuance and financial competence as supply/submission
dates, and comparing pooled leadership spending with one individual allowance.
Removing findings manually would leave those same criteria active on the next
check. Applying a current value to every year would introduce a different
historical error.

Finding records, decisions and annotations are immutable. Missing inputs already
preserve old results. Withdrawing a known unsupported criterion needs a distinct,
auditable lifecycle action; it must not masquerade as a human rejection.

## Decision

### Keep researched schedules separate from legacy assumptions

R01 version 3 and R05 version 2 select frozen `MonthlyCapPolicy` records in
`domain/researched_caps.py`. Each has an inclusive/exclusive competence range,
exact Decimal limit, legal effective date, formula, source URLs and optional
transition limit, amendment date and limitation. No network or database access
occurs in policy selection. Legacy `caps`, `cota_uf` and `adicionais` rows remain
unchanged; these two rules no longer read the legacy cap assumptions.

Coverage is explicitly January 2023 through December 2026. R01 retains the
R$ 12,713.00 cap until the May 2025 amendment and uses R$ 18,970.42 from June.
The latter is calculated from the IPCA index ratio prescribed by
[Ato 196/2025](https://www2.camara.leg.br/legin/int/atomes/2025/atodamesa-196-27-maio-2025-797512-publicacaooriginal-175496-cd-mesa.html)
and the [IBGE indices](https://apisidra.ibge.gov.br/values/t/1737/n1/all/v/2266/p/201704,202502).
May is retained as a review-only transition using the earlier reference, with
the new limit and effective date in evidence.

From March 2026, rental excesses also require review because the additional
reimbursement provision in
[Ato 246/2026](https://www2.camara.leg.br/legin/int/atomes/2026/atodamesa-246-2-marco-2026-798777-publicacaooriginal-178340-cd-mesa.html)
cannot be resolved without individual entitlement evidence.

R05 applies the 25%-of-smallest-quota formula: R$ 7,697.17 in January 2023,
R$ 9,145.62 from February 2023, and R$ 10,403.14 from March 2026.
[Ato 270/2023](https://www2.camara.leg.br/legin/int/atomes/2023/atodamesa-270-19-janeiro-2023-793708-publicacaooriginal-166895-cd-mesa.html)
and [Ato 244/2026](https://www2.camara.leg.br/legin/int/atomes/2026/atodamesa-244-20-fevereiro-2026-798720-publicacaooriginal-178157-cd-mesa.html)
establish the relevant changes; February 2026 is a review-only transition.
All supporting quota sources are retained in the policy records and findings.
`ROUND_HALF_UP` to cents is an explicit computational convention, including
half-cent formula results; it is not presented as a separate statutory rule.

SQL aggregates complete monthly groups, including accounting adjustments.
The pure evaluator then selects the policy. A review-only difference from an
earlier/basic reference is not labeled a confirmed excess.

### Preserve results when coverage is incomplete

Rules can implement `CoverageAwareRule.coverage_reason(row)`. Any uncovered
candidate prevents publication for that whole rule. The run records
`rule.coverage_missing`, uncovered counts and a skipped step. Findings from
covered candidates are not partially published, and previous findings are not
superseded. A new audited year therefore requires policy research before these
rules can replace their previous complete result.

Missing relations and incomplete external-source imports retain their existing
skip/failure behavior. None is evidence that a formerly flagged expense is clear.

### Withdraw unsupported criteria explicitly

`RuleDescriptor.suspended_reason` identifies an intentional versioned suspension.
R10 version 2 requires actual supply and submission dates before reactivation.
R14 version 2 requires pooled leadership entitlement and adjustment history.
Their former proxies cannot establish the rule premise.

The application handles suspension before querying candidate data. In a normal
publishing check it publishes an empty result for the suspended version, using
the existing transactional repository to append supersession events for old
active findings. It records `rule.suspended` with the reason and count and marks
the rule step skipped. It never changes old evidence, decisions or agent traces.
Repeating suspension adds no further supersessions. A dry run records the
suspension without changing findings.

Suspension is not an ordinary source-availability skip and does not certify
lawfulness. Reinstatement requires a new rule version with the necessary source
coverage and regression tests.

## Consequences

Future checks stop recreating these weak findings while retaining their entire
audit trail. New versions of supplier rules separately validate numeric CNPJ
identity, ownership qualifications and the strength of registered-activity
evidence. They retain their mandatory human-review status.

No schema migration is needed. Explanation prompt version 3 and consolidation
prompt version 2 describe these limitations without changing their schemas or
agent authority. Existing human decisions are not automatically copied onto new
finding versions. Production replay on an
isolated database copy checks that the current batch of rejected identities
does not resurface and that repeated publication is idempotent.

R07's historical quota assumptions and individual-role limitations remain
unresolved. R13's principal CNAE is a lead for invoice review, not proof of what
was purchased. Numeric CNPJ validation matches the existing importer scope;
support for other identifier formats requires a separate importer change.
