# Annotate sampled findings with agents

## Purpose and prerequisites

`explain` adds structured opinions and invoice readings to deterministic findings.
It does not change a rule's result, supersede findings, record a human decision,
or approve a final report. Run commands from `opsiu/` after `uv sync`.

First [ingest](ingest.md) the expense data and run a publishing [check](check.md).
Optionally [fetch sampled PDFs](fetch.md) before explaining them:

```sh
uv run python3 -m opsiu check --regras R08,R09
uv run python3 -m opsiu fetch pdf --sample
uv run python3 -m opsiu explain --regras R08,R09 --amostra 5
```

`--regras` selects comma-separated registered rule IDs. Omit it to consider all
registered rules. Unknown or repeated IDs are configuration errors. `--amostra`
sets the maximum findings per rule for this invocation; it must be positive.
Without it, `sampling.per_rule` applies, with default `20`.

## Selection and original evidence

The reusable sampler selects the latest terminal non-dry `check`, with status
`completed` or `completed_with_errors`. Only its successful `rule.checked`
events from completed steps contribute rule IDs and versions. Active findings
matching those rules are sorted by amount descending, then document ID and
finding key ascending, and limited per rule. `--regras` narrows this sample;
it does not search an older check for another rule.

Unchanged findings retain their original creation run. They remain eligible
after a later successful check of the same rule and version. A latest eligible
check with no successful rule steps yields an empty sample without falling back
to older findings. No eligible terminal check causes a recorded selection failure.

Each request uses the finding's original expense record and source hashes, plus
the matching rule text/version. If an extraction exists for the original
document ID and URL, its stored text can accompany the opinion. The invoice
reader then runs separately when usable text exists. This command performs no
PDF download or OCR. Missing text, empty text, or `needs_ocr` skips the invoice
reader while allowing an opinion with the available evidence.

## Scripted and live modes

The default mode is scripted. The command explicitly prints that its responses
are simulated and no provider is called. It uses versioned fixture scenarios,
records their identity, and stores model `scripted`, independently of the model
configured for live execution. These outputs exercise the workflow; they are
not independent invoice analysis or approval.

Live mode requires the literal `AGENTRIG_RUN_LIVE=1` opt-in:

```sh
AGENTRIG_RUN_LIVE=1 uv run python3 -m opsiu explain --regras R08 --amostra 1
```

If `agents.live_env_var` names an additional gate, that variable must also equal
`1`; otherwise the live invocation is rejected as a configuration error. A
custom gate alone does not enable live mode without `AGENTRIG_RUN_LIVE=1`.
Live annotations use `agents.model` and the configured read-only sandbox with
network access denied and no agent tools. The orchestrator supplies the evidence.

`OPSIU_CONFIG` selects the TOML file. Nested environment settings, such as
`OPSIU_AGENTS__MAX_CALLS_PER_RUN`, override file values. Paths remain relative to
the working directory, as for the other commands.

## Call budget and cost

`agents.max_calls_per_run` defaults to `20` and may be zero. The budget counts
annotation attempts: opinions and invoice readings each consume one call, and
failed attempts consume their slot. A failure during request preparation inside
an attempted gateway call can consume budget without reaching the provider.
Already completed annotations and invoice readers skipped for missing text do
not consume calls.

```sh
OPSIU_AGENTS__MAX_CALLS_PER_RUN=2 uv run python3 -m opsiu explain --amostra 1
OPSIU_AGENTS__MAX_CALLS_PER_RUN=0 uv run python3 -m opsiu explain
```

Zero permits no calls. When another annotation would exceed the budget, the
command records `budget.exhausted` and stops; it does not exceed the configured
limit to finish the sample. A budget stop can leave remaining findings or an
invoice reading for a later run. The limit counts calls, not tokens or currency;
live token usage and cost can vary with the supplied text and provider model.
Scripted mode makes no billable provider calls.

## Resume, force, and immutable history

A successful existing annotation for the same finding, agent, annotation kind,
prompt version, and model is skipped. An opinion and an invoice reading are
separate records: rerunning can complete a missing invoice reading even when
the opinion already exists. Newly fetched text does not silently replace an
existing opinion; use force when intentionally requesting another annotation.

```sh
uv run python3 -m opsiu explain --regras R08 --amostra 1 --force
```

`--force` appends new traces and annotation rows within the same call budget.
It never replaces the previous output or reuses a trace identity for a second
annotation. Failed calls remain trace evidence and are not successful resume
markers, so later runs can retry them.

Prompt name/hash must agree with existing successful annotations under the same
version and model. Editing a prompt without changing its version is an integrity
failure, including with `--force`. Make a deliberate prompt-version change and
record it in the changelog instead of rewriting earlier traces or annotations.

## Runs, failures, and trace inspection

Every invocation prints its run ID first and intended `report.md` path last.
The run retains `manifest.json`, `events.jsonl`, and `log.jsonl`; automatic
Markdown run-report rendering remains scheduled for Step 6.2.

```sh
uv run python3 -m opsiu runs show RUN_ID --events
```

The manifest records the source check, selected findings, mode, prompt identities,
original expense origins, and any PDF extraction context. `annotation_calls`
counts budget attempts; `agent_calls` counts invocations with recorded traces.
`annotations_saved`, `annotations_skipped`, `annotations_failed`, and
`budget_exhausted` describe the outcome without exposing private prompt text.

Private `agent_traces` and `agent_trace_records` retain prompt/input, output,
usage, timing, final codec status, and failure evidence with credential redaction.
Successful outputs are appended to `pareceres` only when their finding, run,
step, agent, model, prompt identity, and output match the successful private
trace. Ordinary run events contain metadata rather than invoice text or agent
response bodies.

Typed failures become failed annotation steps; later work continues until the
sample ends or the budget is reached. Earlier successful annotations remain.
Configuration errors exit `2`, operational failures exit `1`, and completed runs
exit `0`. A completed run may still contain skips or budget exhaustion, so inspect
its counters. Human review remains necessary regardless of an agent's confidence
or its advisory `manter`, `descartar`, or `revisar` recommendation.
