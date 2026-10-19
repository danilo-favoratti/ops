# Recommendation evaluations

Run these commands from `opsiu/` after `uv sync --locked`:

```sh
# Scripted baseline, at most the configured 20 calls by default.
AGENTRIG_RUN_LIVE=0 uv run pytest -m eval -s

# Score every labeled case offline in one run.
AGENTRIG_RUN_LIVE=0 OPSIU_AGENTS__MAX_CALLS_PER_RUN=50 uv run pytest -m eval -s
```

`pytest -m "not eval"` runs the ordinary tests, including offline checks of
the dataset, grader, budgets, failures, and run history. It excludes the
evaluation entry point. No production yearly file is used by either suite.

## Dataset and interpretation

[`labeled_findings.jsonl`](labeled_findings.jsonl) contains exactly 50 records,
each with `finding_key`, `expected_recommendation`, and Portuguese `notes`.
The loader rejects duplicate keys, unknown fields, invalid labels, and empty
notes. Recommendations are `manter`, `descartar`, or `revisar`.

[`dataset.py`](dataset.py) pins the SHA-256 of the checked-in
[200-record mini fixture](../tests/fixtures/mini_dataset/Ano-2099.json).
It derives four independent copies in original order. For replica index 0–3,
it adds `index * 100000` to document IDs and `index * 10000` to deputy IDs,
prefixes invoice numbers with `EVAL-{index+1}-`, and updates the synthetic
`example.invalid` document URL. All other fields remain unchanged. This
preserves within-copy duplicate invoices and prevents cross-copy grouping.

The existing deterministic rules R01–R10 and R14 produce 60 findings at the
default 5% quota margin. Sorting by replica, rule ID, and document ID and taking
the first 50 gives all 15 findings from each of the first three copies and
R01–R05 from the fourth. Nondefault quota margins are rejected because these
labels describe that pinned configuration. The configured production year
range does not affect the isolated synthetic year 2099.

These are **synthetic reference labels, without human validation**. They are
not 50 independently audited expenses. There are 26 `manter` labels for exact
cap and CPF signals under the fixture assumptions, and 24 `revisar` labels for
cumulative quota approximations, duplicate signals, and filing-date proxies.
The notes explain each assumption. No `descartar` examples are invented in
the absence of exculpatory fixture facts; grader unit tests cover that label.
External-data rules R11–R13 are outside this first evaluation set.

The unchanged production scripted gateway always emits a conservative
`revisar` annotation. Its full-set agreement is therefore **24/50 = 48%**.
That measures the evaluation pipeline and a constant baseline, not language
model accuracy. Expected labels and notes are supplied only to the grader,
never to the agent request or scripted scenario. The deterministic rules
remain solely responsible for findings; evaluation never approves a finding.

## Sampling, scoring, and evidence

The runner applies `sampling.per_rule`, ordering each rule by amount descending
with stable document/key ties, then visits rules in round-robin order. It stops
at `agents.max_calls_per_run`; failed attempts consume the same budget as
successful calls. The default 20-call budget leaves some cases unscored.
Increasing the budget is explicit; the runner never creates extra runs to
bypass it.

The native AgentRig grader gives exact recommendation agreement a score of 1
and disagreement or missing/invalid output a score of 0. It is an advisory
grade, not a rule verdict. Aggregate agreement is matches divided by attempted
calls, so failed calls cannot improve the result by disappearing. Agreement is
undefined (`null`) with zero attempts. Valid-output coverage is successful
typed outputs divided by the complete labeled set. Counts distinguish the
dataset, selected cases, attempts, successes, failures, and matches.

Each invocation opens a new `eval recommendations` run under
`settings.paths.runs_dir`. Migrations, fixture generation, ingestion, rule
checks, agent invocations, grading, and exports occur within that run. Its
isolated `eval.duckdb` contains the source rows, immutable findings, and private
agent request/response traces. Configured production database, input, and
external-cache paths are untouched. Repeating an evaluation preserves the
previous run and creates new trace IDs.

The run records the dataset hash and produces `eval_results.json`, Portuguese
`eval.md`, and the standard `manifest.json`, `events.jsonl`, and `report.md`.
Per-case scores link back to the actual finding and trace. Keep the run folder
together; its database includes private prompts and responses. An aborted
preparation still leaves a ledger and run report, but may have no score files.

## Live evaluation

The evaluation entry point selects Codex only when `AGENTRIG_RUN_LIVE=1`.
If `agents.live_env_var` names an additional gate, that gate must also equal
`1`. The runtime enforces both gates again before provider execution, with a
read-only sandbox, denied network, strict input/output codecs, and the same
per-run budget. An unset or differently valued literal gate stays scripted.

For an explicitly authorized small live check, configure the desired model and
use `AGENTRIG_RUN_LIVE=1 OPSIU_AGENTS__MAX_CALLS_PER_RUN=1 uv run pytest -m eval -s`.
This uses the locally authenticated Codex runtime and may incur provider usage.
Live scores have no hardcoded accuracy threshold: inspect agreement, coverage,
case-level failures, and private traces. The test checks that the evaluation
completed and produced valid audited outputs; operational failures fail it.
