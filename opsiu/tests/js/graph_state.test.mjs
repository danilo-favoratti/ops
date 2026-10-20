import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../../src/opsiu/interfaces/web/static/graph_state.js", import.meta.url), "utf8");
const {GraphStore, amountToCents, formatCents, layeredPositions} = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const row = (id, amount = "1.00") => ({
  snapshot: "snapshot-1",
  nodes: [
    {id: "deputy:1", kind: "parliamentarian", label: "Deputado", details: {}},
    {id: "supplier:1", kind: "supplier", label: "Fornecedor", details: {}},
    {id: `record:${id}`, kind: "reimbursement", label: `Documento ${id}`, details: {amount}},
  ],
  edges: [
    {id: `left:${id}`, source: "deputy:1", target: `record:${id}`},
    {id: `right:${id}`, source: `record:${id}`, target: "supplier:1"},
  ],
});

test("loading repeated and overlapping pages preserves unique records and signed cents", () => {
  const store = new GraphStore();
  assert.equal(store.merge(row(1, "90071992547409.99")), true);
  assert.equal(store.merge(row(1, "90071992547409.99")), true);
  assert.equal(store.merge(row(2, "-0.01")), true);
  assert.equal(store.nodes.size, 4);
  assert.equal(store.edges.size, 4);
  assert.equal(store.summary().amount, 9007199254740998n);
  assert.equal(formatCents(store.summary().amount), "R$ 90.071.992.547.409,98");
});

test("a budget-exceeding page is rejected atomically without losing connections", () => {
  const store = new GraphStore(3);
  assert.equal(store.merge(row(1)), true);
  assert.equal(store.merge(row(2)), false);
  assert.equal(store.nodes.size, 3);
  assert.equal(store.edges.size, 2);
  assert.equal(store.pageLimit(40), 0);
});

test("invalid or stale pages cannot alter the existing graph", () => {
  const store = new GraphStore();
  store.merge(row(1));
  assert.throws(() => store.merge({...row(2), snapshot: "changed"}), /dados mudaram/);
  assert.throws(() => store.merge({...row(2), edges: [{id: "invalid", source: "missing", target: "record:2"}]}), /sem ponto/);
  assert.equal(store.nodes.size, 3);
  assert.equal(store.edges.size, 2);
});

test("node and request budgets are hard bounded and clear releases all state", () => {
  const store = new GraphStore(100000);
  assert.equal(store.budget, 1000);
  assert.equal(store.pageLimit(10000), 100);
  store.merge(row(1));
  store.clear();
  assert.equal(store.nodes.size, 0);
  assert.equal(store.edges.size, 0);
  assert.equal(store.snapshot, null);
  assert.equal(store.summary().amount, 0n);
});

test("layout is deterministic with finite coordinates and type-separated columns", () => {
  const store = new GraphStore();
  for (let index = 0; index < 100; index += 1) store.merge(row(index));
  const first = layeredPositions(store.nodes, store.edges);
  const second = layeredPositions(store.nodes, store.edges);
  assert.deepEqual(first, second);
  assert.equal(first.get("deputy:1").x, 100);
  assert.equal(first.get("record:0").x, 430);
  assert.equal(first.get("supplier:1").x, 760);
  for (const position of first.values()) assert.ok(Number.isFinite(position.x) && Number.isFinite(position.y));
});

test("money parsing keeps cent accuracy and rejects malformed values", () => {
  assert.equal(amountToCents("1348500.00"), 134850000n);
  assert.equal(formatCents(134850000n), "R$ 1.348.500,00");
  assert.equal(amountToCents("-15.3"), -1530n);
  assert.equal(amountToCents("NaN"), 0n);
  assert.equal(amountToCents("1.234"), 0n);
});
