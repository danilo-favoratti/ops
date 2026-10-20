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

Use `uv run python3 -m opsiu review --help` to inspect startup options without
opening a listener.

The same server provides the [reimbursement graph](graph.md) at `/graph`.
It explores all current ingested expense records and does not require findings.

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

The header shows the pending count and total flagged amount for all findings
matching the selected filters, across every page. Both use the same pending
selection. Amounts display as Brazilian reais (for example, `R$ 1.348.500,00`).
The total sums finding amounts without deduplicating expenses across rules or
cumulative periods; the interface notes that these amounts can overlap.

The list is available at `/` and `/findings`; query parameters are `rule`,
`deputy`, `uf`, `min_amount`, and `offset`. Amounts accept a decimal dot or comma,
for example `min_amount=100.50` or `min_amount=100,50`, without thousands
separators. Each page contains at most 100 findings, ordered by severity
(`alta`, `media`, `revisar`), amount descending, then finding key. Filters use
the finding's original expense identity. The finding detail is
`/findings/{finding_key}`.

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

The archived-PDF link, `/findings/{finding_key}/pdf`, serves the verified local
PDF cache. A missing cache returns `404`; a path, hash, or size conflict returns
`409` instead of serving uncertain bytes. A separate original-document link
opens the source URL when it is valid HTTP(S). Use the
[PDF fetch workflow](fetch.md) to capture missing invoices or explicitly refresh
a failed cache. A rule-text version mismatch is shown as unavailable evidence
rather than silently substituting a newer rule document.

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
decision, reviewer, comment, timestamp, run, and step. The latest timestamp is
current, with the highest decision ID breaking a timestamp tie; earlier
decisions remain in the history. A later decision appends a new row rather than
replacing the earlier judgment. Approving a finding does not change its severity
or deterministic evidence.

The browser updates the decision section through locally bundled HTMX, with a
normal detail-page redirect when submitting without JavaScript. Styles and
scripts are served locally without a CDN. Decision history is chronological.
Forms include a signed CSRF token tied to a same-site cookie; if a form is
rejected because its token is stale or missing, reload the local detail page
before resubmitting. Restarting the server also invalidates earlier form tokens.
Requests use a loopback host. The Origin header, or Referer when Origin is
absent, must match the local page's origin when supplied.
Cross-site requests identified by `Sec-Fetch-Site` are rejected as well.
An invalid host returns `400`; invalid CSRF tokens or origins return `403`.

Agent recommendations never change this queue automatically. A recommendation
to discard a finding still requires a human decision. After review, use
[`report final`](report.md) to export active findings whose latest human decision
is `aprovado`, with decision provenance and opinion summaries.

## Runs, failures, and verification

The launch command prints its run ID first and its `report.md` path when the
server finishes. Finalization automatically generates
`paths.runs_dir/<run_id>/report.md` with the command, duration, step outcomes,
counters, failures, and registered artifacts. Each decision has an independent
run identity and report. Inspect the report and its links to `manifest.json`,
`events.jsonl`, and `log.jsonl`, or use:

```sh
uv run python3 -m opsiu runs show RUN_ID --events
```

Page, PDF, and local asset requests also create audit runs. The
`X-OPSIU-Run-ID` response header identifies the request's run; the page includes
the run identity for inspection. Browser reads therefore appear alongside
decisions in execution history and receive their own reports on finalization.

Decision events contain identifiers and decision metadata. The comment remains
in the decision record rather than ordinary event text. Missing or superseded
findings and invalid input are rejected without appending a decision. Earlier
decisions remain intact. If a response or finalization fails after a decision
has been saved, that decision remains recorded; inspect the finding's history
before resubmitting.

A rejected CSRF/origin check joins an existing database audit ledger without
running migrations or reading review data. If the database is missing or
unavailable, the rejection still retains its file-based run evidence.

`runs show` prints the report path. See [run history and reports](runs.md) for
the inspection workflow, exit codes, and the behavior when report generation
itself fails.
