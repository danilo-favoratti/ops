# Explore reimbursement connections

After ingestion, start the existing review application:

```sh
cd opsiu
uv run python3 -m opsiu review
```

Open `http://127.0.0.1:8765/graph`, or select **Grafo de reembolsos** in the
navigation. No check, external supplier download or agent call is required.
The graph reads the current ingested accounting rows, including negative and
zero adjustments. A connection is a relationship in those records, not a
finding or a conclusion of wrongdoing.

## Search and explore

Search for a parliamentarian by name or code, or a supplier by name or CPF/CNPJ,
then select a suggestion. Searches ignore case and accents. Year and general
record search further narrow the neighborhood. Apply the filters with
**Explorar conexões**.

Teal circles represent parliamentarians, amber diamonds represent expense
records, and purple squares represent suppliers. Drag to pan, scroll to zoom,
or use the zoom/fit buttons. Keyboard arrows move the map, plus/minus zoom, and
zero fits it. The point selector provides a keyboard-accessible alternative to
clicking a node.

Select a point to inspect its details and connected records. **Explorar este
ponto** starts a new neighborhood for that parliamentarian or supplier, retaining
the year and general search filters. **Carregar mais** adds the next page.
Nothing automatically walks the entire dataset.

The header amount is the exact sum of loaded expense net amounts, including
adjustments, displayed in Brazilian currency. Loaded counts differ from the
server's full filtered counts and total. These are reimbursement accounting
amounts, not corruption estimates or sums of findings.

## Identity and provenance

Each expense node uses its source-file ID and record number. Repeated invoice
IDs, identical accounting rows and records without usable document IDs remain
separate nodes. Details retain the original document code, source identity and
hashes. A newly ingested annual source replaces the graph's current view; its
predecessor and audit history are preserved.

Parliamentarians connect by their source deputy code. Suppliers connect by
numeric CPF/CNPJ only when its length and check digits are valid. Check-digit
validation does not confirm registry status or legal ownership. Missing or
invalid IDs are isolated per accounting row, even when names match, to avoid
inventing relationships. The details panel identifies that limitation.

## Loading limits and diagnostics

The default page contains at most 40 expenses and the browser holds at most
500 nodes. **Diagnóstico e controles** offers pages of 20, 40 or 100 records,
node budgets of 200, 500 or 1,000, a label toggle, animation pause, layout reset
and memory release.

Every page is bounded server-side to 100 expenses, 300 nodes and 200 edges.
Browser limits are 1,000 nodes, 2,000 edges, 36 labels and 25 request-log entries.
Loading stops before a full next page would exceed the selected node budget.
Search suggestions are limited to 12 in the interface and 20 in the API.
Changing an applied filter or page size starts a new exploration and releases
the old graph; late responses cannot repopulate it. Reducing the node budget
below the current count also resets the graph; otherwise budget changes keep
the current neighborhood. There is no full-dataset client cache.

Diagnostics show current object counts, request latency and response size,
renderer information and measured frame timing. Browser heap memory is shown
only when supported; it covers the JavaScript heap, not total GPU/process memory.
Rendering happens on changes, with a short optional layout animation. Quiet
maps do not run an ongoing force simulation.

The browser needs WebGL. An unavailable renderer shows an explanatory message.
PixiJS and all application assets are self-hosted; no CDN is used and the
existing `script-src 'self'` policy remains in force. Rebuilding the vendor asset
is a developer-only Node task described in the
[static asset README](../../src/opsiu/interfaces/web/static/README.md).

## API and audit history

- `GET /graph` renders the page in a `graph.page` run.
- `GET /api/graph` accepts `deputy_id`, `supplier_id`, `year`, `query`, `cursor`
  and `limit`, and creates a `graph.data` run.
- `GET /api/graph/search` accepts `kind` (`parliamentarian` or `supplier`),
  `q`, `year` and `limit`, and creates a `graph.search` run.

Use the opaque `next_cursor` with exactly the same filters and page size.
The cursor binds the source snapshot and last accounting-row identity, so
pages do not silently mix annual source versions. A changed source snapshot
returns HTTP 409 and asks the user to restart. Malformed or conflicting
parameters return HTTP 400. Responses carry `X-OPSIU-Run-ID` and are not cached.

Graph requests use the same local-host restriction and run ledger as review.
They append execution evidence without changing expenses, findings, decisions,
or agent history. Debug controls affect the browser only.

## Verify

```sh
uv run pytest tests/unit/test_graph_repository.py tests/integration/test_graph_web.py
node --test tests/js/graph_state.test.mjs
```

The Python tests use synthetic data and cover accounting identity, adjustments,
bounded traversal, source replacement, filters, search, HTTP validation and
durable run evidence. The JavaScript tests exercise budget enforcement,
atomic merges, snapshot changes, exact currency arithmetic and layout bounds.
