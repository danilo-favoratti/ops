/** Bounded graph state, independent of the renderer and network. */
export const HARD_NODE_LIMIT = 1000;
export const HARD_EDGE_LIMIT = 2000;
export const NODE_KINDS = ["parliamentarian", "reimbursement", "supplier"];

export function amountToCents(value) {
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(String(value));
  if (!match) return 0n;
  const cents = BigInt(match[2]) * 100n + BigInt((match[3] || "").padEnd(2, "0"));
  return match[1] ? -cents : cents;
}

export function formatCents(cents) {
  const negative = cents < 0n;
  const absolute = negative ? -cents : cents;
  return `${negative ? "−" : ""}R$ ${new Intl.NumberFormat("pt-BR").format(absolute / 100n)},${String(absolute % 100n).padStart(2, "0")}`;
}

export class GraphStore {
  constructor(budget = 500) {
    this.budget = Math.min(HARD_NODE_LIMIT, Math.max(1, budget));
    this.nodes = new Map();
    this.edges = new Map();
    this.snapshot = null;
  }

  clear() {
    this.nodes.clear();
    this.edges.clear();
    this.snapshot = null;
  }

  pageLimit(requested = 40) {
    return Math.max(0, Math.min(100, requested, Math.floor((this.budget - this.nodes.size) / 3)));
  }

  merge(page) {
    if (!page || !Array.isArray(page.nodes) || !Array.isArray(page.edges)) {
      throw new Error("Resposta do mapa inválida.");
    }
    if (page.nodes.length > 300 || page.edges.length > 200) {
      throw new Error("O lote ultrapassou o limite de segurança do mapa.");
    }
    if (this.snapshot && this.snapshot !== page.snapshot) {
      throw new Error("Os dados mudaram durante a consulta. Reinicie a exploração.");
    }
    const additions = new Map();
    for (const node of page.nodes) {
      if (typeof node.id !== "string" || !NODE_KINDS.includes(node.kind)) {
        throw new Error("Ponto inválido na resposta do mapa.");
      }
      if (!this.nodes.has(node.id)) additions.set(node.id, node);
    }
    const edges = new Map();
    for (const edge of page.edges) {
      if (typeof edge.id !== "string" ||
          !(this.nodes.has(edge.source) || additions.has(edge.source)) ||
          !(this.nodes.has(edge.target) || additions.has(edge.target))) {
        throw new Error("Conexão sem ponto correspondente.");
      }
      if (!this.edges.has(edge.id)) edges.set(edge.id, edge);
    }
    if (this.nodes.size + additions.size > this.budget ||
        this.edges.size + edges.size > HARD_EDGE_LIMIT) return false;
    for (const [id, node] of additions) this.nodes.set(id, node);
    for (const [id, edge] of edges) this.edges.set(id, edge);
    this.snapshot = page.snapshot;
    return true;
  }

  summary() {
    const counts = {parliamentarian: 0, reimbursement: 0, supplier: 0, amount: 0n};
    for (const node of this.nodes.values()) {
      counts[node.kind] += 1;
      if (node.kind === "reimbursement") counts.amount += amountToCents(node.details?.amount);
    }
    return counts;
  }
}

/** Stable three-column layout: linear work plus sorting, no pairwise forces. */
export function layeredPositions(nodes, edges) {
  const groups = Object.fromEntries(NODE_KINDS.map((kind) => [kind, []]));
  const neighboring = new Map();
  for (const node of nodes.values()) {
    groups[node.kind].push(node);
    neighboring.set(node.id, []);
  }
  for (const edge of edges.values()) {
    neighboring.get(edge.source)?.push(edge.target);
    neighboring.get(edge.target)?.push(edge.source);
  }
  groups.reimbursement.sort((a, b) => {
    const relation = (node) => (neighboring.get(node.id) || [])
      .filter((id) => nodes.get(id)?.kind === "parliamentarian").join("");
    return relation(a).localeCompare(relation(b)) || a.id.localeCompare(b.id);
  });
  const positions = new Map();
  const height = Math.max(420, groups.reimbursement.length * 25);
  groups.reimbursement.forEach((node, index) => {
    positions.set(node.id, {x: 430, y: ((index + 0.5) / Math.max(1, groups.reimbursement.length)) * height});
  });
  for (const [kind, x] of [["parliamentarian", 100], ["supplier", 760]]) {
    const desired = new Map();
    for (const node of groups[kind]) {
      const ys = (neighboring.get(node.id) || []).map((id) => positions.get(id)?.y).filter((y) => y !== undefined);
      desired.set(node.id, ys.length ? ys.reduce((sum, value) => sum + value, 0) / ys.length : height / 2);
    }
    groups[kind].sort((a, b) => desired.get(a.id) - desired.get(b.id) || a.id.localeCompare(b.id));
    let previous = -30;
    for (const node of groups[kind]) {
      const y = Math.max(previous + 30, desired.get(node.id));
      positions.set(node.id, {x, y});
      previous = y;
    }
  }
  return positions;
}
