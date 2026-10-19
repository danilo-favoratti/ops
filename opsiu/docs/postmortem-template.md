# Post-mortem: <incident title>

- Incident date: <UTC timestamp>
- Owner: <name>
- Related run IDs: <IDs and links to manifests/reports>
- Status: <investigating / resolved / follow-up pending>

## Summary

Describe the expected behavior, observed failure, and how it was detected.

## Timeline

Reconstruct the sequence from the run ledger and `events.jsonl`. Include UTC
timestamps, run IDs, step IDs, commands, failures, and recovery actions. Separate
recorded evidence from assumptions.

## Impact

Identify affected source files, finding keys and versions, agent traces, human
decisions, and exported reports. State the affected period and uncertainty.
Record whether any approved findings were exported incorrectly.

## Root cause

Explain the failing mechanism, contributing conditions, and why existing checks
did not catch it. Link the relevant code and evidence.

## What the manifest showed

Record the command and arguments, resolved settings hash, code commit and dirty
flag, Python/package versions, source hashes, snapshot IDs, rule versions, model,
prompt versions, and counters relevant to reproduction. Mark unavailable fields
explicitly; do not infer them from the current environment.

## Corrective actions

For each action, record an owner, tracking issue, expected outcome, and validation.
Preserve original findings, decisions, snapshots, and traces. Record replacement
or superseding results in a new run, with the required rule, prompt, or migration
version change in the changelog.

## Reproduction and recovery

Describe how to check out the recorded commit, restore the recorded snapshots,
and execute the original command with matching configuration in an isolated
workspace. Record recovery run IDs and the evidence confirming the result.

## Follow-up architecture decision

Link an ADR if recovery changes the design. Otherwise explain why the existing
architecture remains appropriate.
