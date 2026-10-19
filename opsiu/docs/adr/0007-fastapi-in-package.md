# ADR 0007: A local FastAPI review app in the package

Status: Accepted; implementation is scheduled in Phase 5.

## Context

Human approval is required before findings enter the final report. Reviewers need
to inspect rule evidence, source records, invoices, and agent annotations without
manually editing database rows.

## Decision

Implement the review interface with FastAPI inside the existing `opsiu` package.
Render Portuguese pages with Jinja2 and use HTMX for partial updates. Start the
local app through the `review` CLI command; no separate frontend build is required.

Routes call `application.review` directly. Provide a filtered findings list,
finding detail with provenance and invoice material, and review actions with a
comment. Each decision creates a run containing one step and appends a decision
row. Read the reviewer identity from settings. Support a single local user.

## Consequences

The web interface shares application logic, settings, migrations, and run tracking
with the CLI. Server-rendered templates keep installation and testing within the
Python package. Multi-user authentication and a separately deployed frontend are
outside this decision; changing the deployment model requires a new architectural
decision. FastAPI test-client coverage must verify both rendered pages and the
decision/run trail.

Source: [web review interface](../../ARCHITECTURE.md#web-review-app).
