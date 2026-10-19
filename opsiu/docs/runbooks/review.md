# Review findings locally

## Purpose and prerequisites

`review` starts the Portuguese review interface for one local reviewer. It reads
deterministic findings and their original evidence, then records human decisions
without changing rule results or removing earlier decisions.

Run commands from `opsiu/` after `uv sync`. First [ingest](ingest.md) the source
data and publish findings with [check](check.md). [Agent opinions](explain.md)
and [PDF capture](fetch.md) are optional evidence for review.

```sh
uv run python3 -m opsiu review
uv run python3 -m opsiu review --port 8766
```

The default address is `http://127.0.0.1:8765`. An explicit port changes only the
port; the listener remains on `127.0.0.1`. Open the displayed local address in a
browser. Keep the terminal running while reviewing and stop the server with
Ctrl+C when finished.

Startup applies pending schema migrations before serving requests. Ports must
be integers from `1` through `65535`; malformed and out-of-range values fail
inside an audited launch run. Migration, database-open, and server-start failures
also leave run evidence and do not report a successful startup.

`OPSIU_CONFIG` selects the TOML configuration. The database, run directory, and
external PDF paths use the same settings as the other commands. Set the human
reviewer name before starting the application:

```toml
[review]
reviewer = "Revisora local"
```

`OPSIU_REVIEW__REVIEWER` overrides that value. The default is `local`. This value
is an attribution label for the local workflow, not a sign-in identity.

## Select and inspect evidence

The pending queue contains active findings without a human decision and those
whose latest decision is `revisar`. Filter by rule, deputy ID, UF, and minimum
amount. The amount belongs to the finding's rule; it is not automatically a
confirmed loss or an amount to recover.

The list is available at `/` and `/findings`; query parameters are `rule`,
`deputy`, `uf`, `min_amount`, and `offset`. Amounts accept a decimal dot or comma,
for example `min_amount=100.50` or `min_amount=100,50`, without thousands
separators. The finding detail is `/findings/{finding_key}`.

Open a finding to inspect its original expense record, deterministic rule text
and version, evidence, agent annotations, available PDF/text, and decision
history. Original source identity and hashes remain attached even if a later
ingestion replaces the current expense projection. Superseded findings retain
their historical evidence and decisions but cannot receive new decisions.

Read agent opinions as advisory annotations. A `scripted` model identifies
simulated fixture responses rather than independent analysis. Live agent
confidence or recommendations do not constitute human approval. Missing PDFs,
missing extracted text, and `needs_ocr` leave evidence gaps; review does not
download invoices, perform OCR, or run agents automatically.

The invoice link serves the verified local PDF cache. A missing cache returns
`404`; a path, hash, or size conflict returns `409` instead of serving uncertain
bytes. Use the [PDF fetch workflow](fetch.md) to capture missing invoices or
explicitly refresh a failed cache. A rule-text version mismatch is shown as
unavailable evidence rather than silently substituting a newer rule document.

Keep the rule's limitations visible when deciding. In particular, accumulated
quota findings and duplicate-invoice findings can overlap in amount. Shared
surnames do not establish kinship, and current company registration does not
prove ownership or invoice contents at the expense date.

## Decisions and history

Record `aprovado` to approve a finding for subsequent reporting, `rejeitado` to
reject it, or `revisar` to keep it pending for further examination. A comment
can explain the evidence and uncertainty behind the decision. An empty comment
is allowed; the configured reviewer name must be nonempty.

Every submitted decision creates its own `review.decide` run with one
`registrar_decisao` step. The stored decision includes the exact finding key,
decision, reviewer, comment, timestamp, run, and step. The latest decision is
current; earlier decisions remain in the history. A later decision appends a
new row rather than replacing the earlier judgment. Approving a finding does
not change its severity or deterministic evidence.

The browser updates the decision section through HTMX, with a normal detail-page
redirect when submitting without HTMX. Decision history is chronological. Forms
include a CSRF token tied to a same-site cookie; if a form is rejected because
its token is stale or missing, reload the local detail page before resubmitting.
Requests use a loopback host. The Origin header, or Referer when Origin is
absent, must match the local page's origin when supplied.
An invalid host returns `400`; invalid CSRF tokens or origins return `403`.

Agent recommendations never change this queue automatically. A recommendation
to discard a finding still requires a human decision. The review interface
records approval; the approved-only final export is introduced separately in
the implementation plan.

## Runs, failures, and verification

The launch command prints its run ID first and intended `report.md` path when
the server finishes. Each decision has an independent run identity. Inspect
the corresponding `manifest.json`, `events.jsonl`, and `log.jsonl`, or use:

```sh
uv run python3 -m opsiu runs show RUN_ID --events
```

Page, PDF, and local asset requests also create audit runs. The
`X-OPSIU-Run-ID` response header identifies the request's run; the page includes
the run identity for inspection. Browser reads therefore appear alongside
decisions in execution history.

Decision events contain identifiers and decision metadata. The comment remains
in the decision record rather than ordinary event text. Failed requests do not
produce a successful decision: missing or superseded findings, invalid input,
and storage errors are recorded as failures, and prior decisions remain intact.

A rejected CSRF/origin check joins an existing database audit ledger without
running migrations or reading review data. If the database is missing or
unavailable, the rejection still retains its file-based run evidence.

Automatic Markdown run reports are scheduled for Step 6.2. The current audit
artifacts and decision history provide the verification trail in the meantime.
