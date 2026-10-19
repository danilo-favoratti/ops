# Synthetic CEAP fixtures

`Ano-2099.json` contains exactly 200 records in the source-compatible `dados`
array. Every record includes all 31 source fields and parses with
`Despesa.from_raw`. Document identifiers are unique, from `209900001` through
`209900200`. There are 15 deputy/leadership identities, including one leadership
identity represented by three records.

Names and records are synthetic. CPF values deliberately have invalid check
digits, the CNPJ is the synthetic `00.000.000/0001-00`, and every invoice URL uses
`example.invalid`. No external requests are needed. Year 2099 isolates the fixture
from the product's 2023–2026 input years; tests explicitly select 2099. Published
policy amounts are reused only to exercise the planned deterministic comparisons.

`five_records.json` is the separate streaming-parser fixture. Its intentionally
incomplete records are not suitable for the ingest use case.

## R01–R06: monthly caps

Deputy `101`, `Deputada Sintética dos Limites`, belongs to DF. Each category has
two documents per month, so a check must aggregate records. January totals exceed
the cap by BRL 0.01, February totals are BRL 0.01 below it, and March totals equal
it. Only January should trigger each monthly cap rule. Every individual document
is below its category's cap.

The following identifiers abbreviate the shared `209900` prefix:

- R01, category `120`, cap BRL 12,713.00: January `001–002`, February `003–004`,
  March `005–006`; positive finding attaches to document `209900002`.
- R02, category `122`, cap BRL 2,700.00: January `007–008`, February `009–010`,
  March `011–012`; positive finding attaches to document `209900008`.
- R03, category `3`, cap BRL 9,392.00: January `013–014`, February `015–016`,
  March `017–018`; positive finding attaches to document `209900014`.
- R04, category `8`, cap BRL 8,700.00: January `019–020`, February `021–022`,
  March `023–024`; positive finding attaches to document `209900020`.
- R05, category `137`, cap BRL 7,697.17: January `025–026`, February `027–028`,
  March `029–030`; positive finding attaches to document `209900026`.
- R06, category `40`, cap BRL 4,148.80: January `031–032`, February `033–034`,
  March `035–036`; positive finding attaches to document `209900032`.

Invoice numbers identify the rule, comparison, and document part, for example
`R01-ACIMA-A` and `R01-ACIMA-B`. Other scenarios do not add spending to deputy 101.

## R07: state quota, carryover, and the margin

The DF monthly quota is BRL 41,612.55. At the planned default 5% margin, the
January threshold is BRL 43,693.1775 before any currency rounding.

- Deputy `101` spends BRL 45,351.03 in January, BRL 45,350.91 in February, and
  BRL 45,350.97 in March. All three months are quota-positive, including the
  otherwise negative and boundary cap months. Their latest document identifiers
  are `209900032`, `209900034`, and `209900036`.
- Deputy `501` spends BRL 1,000.00 in January (`209900051`) and BRL 70,000.00
  in February (`209900052`). The combined BRL 71,000.00 is within two monthly
  quotas, exercising a large month covered by carryover.
- Deputy `502` spends BRL 42,000.00 in January (`209900053`), exceeding the base
  quota but remaining inside the 5% margin.
- Deputy `503` spends BRL 43,693.17 in January (`209900054`), just below the
  margin threshold. Deputy `504` spends BRL 43,693.18 (`209900055`), just above
  it and expected to trigger R07.

All four dedicated quota-control deputies belong to DF. Their spending uses
category `1`, so no monthly cap scenario is affected.

## R08: payments to individuals

Deputy `201` has five May payments of BRL 25.00 to synthetic CPF
`000.000.000-01`:

- `209900040`, category `3`: prohibited-category positive case.
- `209900041`, category `1`: permitted property-rent negative case.
- `209900042`, category `119`: permitted aircraft-charter negative case.
- `209900043`, category `121`: permitted boat-charter negative case.
- `209900044`, category `122`: permitted taxi negative case.

All other fixture payees are classified as CNPJ by normalized identifier length.

## R09: duplicate invoices

Documents `209900045` and `209900046` share payee, invoice number
`R09-DUPLICADA`, gross amount BRL 321.09, and issue date 2099-06-01, but belong to deputies `301` and
`302`. Both documents should be findings. The second document has a BRL 1.00
deduction and net amount BRL 320.09, checking that duplicate grouping uses the
gross `valorDocumento` field.

Document `209900047` uses the same payee and gross amount with invoice number
`R09-NUMERO-DIFERENTE`, a negative control. The deliberate duplicate pair is the
only repeated invoice number anywhere in the fixture.

## R10: filing deadline boundaries

Deputy `401` has three July payments. Measure elapsed days from the issue date
to `2099-07-01`, the first day of the reimbursement month:

- `209900048`: issued `2099-04-01`, 91 days earlier; positive case.
- `209900049`: issued `2099-04-02`, exactly 90 days earlier; negative boundary.
- `209900050`: issued `2099-04-03`, 89 days earlier; negative case.

Other records are issued on the first day of their reimbursement month.

## R14: leadership quota

Deputy identifier `1001` is named `LIDERANÇA DO GOVERNO NA CÂMARA`, with empty
`cpf`, empty parliamentary-card and party fields, and `siglaUF = "NA"`. These
source values must produce `lideranca = true` during ingest.

- `209900037`: January spending BRL 1,500.00 exceeds the BRL 1,353.04 monthly
  leadership allowance, including the default 5% margin; positive R14 case.
- `209900038`: February spending BRL 1,200.00 brings cumulative spending to
  BRL 2,700.00, within two allowances; negative case.
- `209900039`: March spending equals one allowance, BRL 1,353.04. Cumulative
  spending remains within three allowances; negative case.

These rows use category `1` and are dedicated to the leadership variant rather
than the state-quota calculation.

## Remaining records and fixture invariants

Documents `209900056–209900200` are 145 CNPJ payments of BRL 1.00 in category
`1` during August. Deputies `600–604` each have 29 records. Invoice numbers
`CONTROLE-001` through `CONTROLE-145` are unique. These rows exercise ingest
volume while staying below quota thresholds and outside capped categories.

The fixture has six positive monthly cap groups, four positive state-quota
months across two deputies, one prohibited CPF payment, one duplicate group
with two documents, one late filing, and one positive leadership month. These
are expected scenarios for Phase 2, not a claim that rule evaluation already
exists in the fixture-creation step. External-source rules R11–R13 require their
own later snapshot fixtures.
