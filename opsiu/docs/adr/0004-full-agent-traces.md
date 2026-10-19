# ADR 0004: Retain full AgentRig traces

Status: Accepted; implementation is scheduled in Phase 4.

## Context

Agent explanations and invoice readings are probabilistic. An annotation is useful
only when its inputs, prompt, output, and execution can be inspected alongside the
deterministic finding it discusses.

## Decision

Use typed AgentRig contracts and strict codecs. Agents annotate existing findings;
they do not determine rule outcomes. Configure read-only execution, denied network
access, one turn, and no tool calls.

Implement `PrivateTraceRecorder` to append full prompt, input, response, model,
usage, duration, and status to `agent_traces`. Associate traces with the finding
and the application's run identifier. Bridge AgentRig events into the same run
ledger and link stored annotations to their traces.

Keep Portuguese prompts in versioned task files and record their version and
SHA-256 hash. Invalid outputs are recorded failures. Use scripted runtimes for
offline tests; live Codex requires `AGENTRIG_RUN_LIVE=1` and a configured per-run
call budget.

## Consequences

Reviewers can inspect how an annotation was produced, and evaluations can use the
same contracts without live calls. Full traces consume more storage and can
contain the original invoice content, so they remain part of the local audit data.
Prompt changes require versioning and changelog entries; reruns retain prior traces.

Source: [AgentRig adapters](../../ARCHITECTURE.md#agents-agentrig).
