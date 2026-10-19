# ADR 0001: One package with four layers

Status: Accepted.

## Context

OPSIU combines deterministic reimbursement rules, local storage, external data,
agent annotations, and CLI and web interfaces. Rule logic must remain testable
without a database, network access, or an agent runtime.

## Decision

Keep one installable Python package under `src/opsiu`, managed by uv and requiring
Python 3.12 or newer. Organize it into domain, application, adapters, and interfaces.

The domain contains pure types, policies, and rules with no I/O. Application use
cases depend on the domain and application ports declared as `Protocol` classes.
Adapters implement those ports. Interfaces compose adapters and call application
use cases; they do not own audit logic. Dependencies point inward, and domain code
never imports another layer.

## Consequences

The CLI and web app share use cases, provenance, and run tracking. Tests can inject
scripted agents, clocks, identifiers, and storage adapters. There is one dependency
lockfile and release unit, while port definitions and explicit composition add a
small amount of code. New integrations must preserve the layer boundaries.

Source: [technical architecture](../../ARCHITECTURE.md#layers-and-package-layout).
