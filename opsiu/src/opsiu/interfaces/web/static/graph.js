import {GraphStore, NODE_KINDS, amountToCents, formatCents, layeredPositions} from "./graph_state.js";

const root = document.querySelector("#graph-page");
if (root) startGraph();

async function startGraph() {
  const $ = (id) => document.getElementById(`graph-${id}`);
  const names = {parliamentarian: "Parlamentar", reimbursement: "Despesa", supplier: "Fornecedor"};
  const colors = {parliamentarian: 0x277b87, reimbursement: 0xce932e, supplier: 0x8260af};
  const store = new GraphStore(500);
  const lifetime = new AbortController();
  const views = new Map();
  const labels = new Map();
  const logs = [];
  const selectedFilters = {parliamentarian: null, supplier: null};
  const searches = [];
  const metrics = {requests: 0, bytes: 0, latency: 0, frames: 0, fps: 0};
  let app = null, world = null, lines = null, nodeLayer = null, labelLayer = null;
  let selected = null, hover = null, cursor = null, hasMore = true, busy = false;
  let generation = 0, graphRequest = null, activeFilters = {}, pageLimit = 40;
  let paused = false, rendererFailed = false, disposed = false, budgetBlocked = false;
  let animation = 0, resizeObserver = null, lastFrame = 0, tickStarted = performance.now(), metricInterval = null;
  let graphBounds = {left: -110, top: -60, right: 1050, bottom: 500};
  const listen = (element, event, callback, options = {}) =>
    element.addEventListener(event, callback, {...options, signal: lifetime.signal});

  function overlay(title, text) {
    $("overlay").hidden = !title;
    $("overlay-title").textContent = title || "";
    $("overlay-text").textContent = text || "";
  }

  function render() {
    if (!app || disposed) return;
    app.render();
    metrics.frames += 1;
  }

  function setStatus(message) {
    $("status").textContent = message;
    $("load-more").disabled = busy || budgetBlocked || !hasMore;
    $("load-more").textContent = busy ? "Carregando…" : budgetBlocked ? "Limite alcançado" :
      hasMore ? "Carregar mais" : "Todos carregados";
    $("canvas").setAttribute("aria-busy", String(busy));
  }

  function recordRequest(label, bytes, elapsed, status) {
    metrics.requests += 1;
    metrics.bytes += bytes;
    metrics.latency = elapsed;
    logs.unshift(`${label} · ${status} · ${Math.round(elapsed)} ms · ${(bytes / 1024).toFixed(1)} KB`);
    if (logs.length > 25) logs.length = 25;
    $("request-log").replaceChildren(...logs.map((message) => {
      const item = document.createElement("li");
      item.textContent = message;
      return item;
    }));
    updateMetrics();
  }

  async function request(path, params, controller, label) {
    const started = performance.now();
    let bytes = 0, status = "interrompida";
    try {
      const response = await fetch(`${path}?${params}`, {signal: controller.signal, headers: {Accept: "application/json"}});
      const text = await response.text();
      bytes = new TextEncoder().encode(text).byteLength;
      status = String(response.status);
      if (bytes > 2_000_000) throw new Error("O lote recebido ultrapassou o limite de segurança.");
      const data = JSON.parse(text);
      if (!response.ok) throw new Error(data.error || data.message || "Não foi possível consultar os dados.");
      return data;
    } catch (error) {
      if (error.name !== "AbortError") status = `erro (${status})`;
      throw error;
    } finally {
      if (!disposed) recordRequest(label, bytes, performance.now() - started, status);
    }
  }

  function updateMetrics() {
    const counts = store.summary();
    $("amount").textContent = formatCents(counts.amount);
    $("loaded").textContent = `${counts.reimbursement.toLocaleString("pt-BR")} registros · ${counts.parliamentarian} parlamentares · ${counts.supplier} fornecedores`;
    $("debug-summary").textContent = `${store.nodes.size} pontos · ${store.edges.size} conexões`;
    const memory = performance.memory ? `${(performance.memory.usedJSHeapSize / 1048576).toFixed(1)} MB (página)` : "Não informado pelo navegador";
    const values = [
      ["Pontos / limite", `${store.nodes.size} / ${store.budget}`],
      ["Conexões / limite", `${store.edges.size} / 2000`],
      ["Parlamentares / despesas / fornecedores", `${counts.parliamentarian} / ${counts.reimbursement} / ${counts.supplier}`],
      ["Requisições / dados", `${metrics.requests} / ${(metrics.bytes / 1024).toFixed(1)} KB`],
      ["Última resposta", `${Math.round(metrics.latency)} ms`],
      ["Renderização", rendererFailed ? "WebGL indisponível" : app ? "PixiJS · WebGL" : "Iniciando"],
      ["Quadros renderizados / s", `${metrics.fps} (sob demanda)`],
      ["Textos / limite", `${labels.size} / 36`],
      ["Memória JavaScript", memory],
      ["Carregamento", budgetBlocked ? "Pausado pelo limite" : busy ? "Consultando" : hasMore ? "Sob demanda" : "Consulta completa"],
    ];
    $("debug-metrics").replaceChildren(...values.map(([label, value]) => {
      const group = document.createElement("div"), term = document.createElement("dt"), detail = document.createElement("dd");
      term.textContent = label; detail.textContent = value; group.append(term, detail); return group;
    }));
  }

  function clearObjects() {
    if (animation) cancelAnimationFrame(animation);
    animation = 0;
    for (const graphic of views.values()) graphic.destroy({children: true, context: true});
    for (const label of labels.values()) label.destroy({texture: true, textureSource: true});
    views.clear(); labels.clear();
    if (lines) {
      lines.destroy({context: true});
      lines = null;
      if (world && !disposed) { lines = new PIXI.Graphics(); world.addChildAt(lines, 0); }
    }
    selected = null; hover = null;
    $("node-select").replaceChildren(new Option("Escolha para consultar os detalhes", ""));
    $("detail-empty").hidden = false; $("detail-content").hidden = true;
  }

  function reset() {
    generation += 1;
    graphRequest?.abort(); graphRequest = null;
    for (const search of searches) search.cancel();
    clearObjects(); store.clear();
    cursor = null; hasMore = true; busy = false; budgetBlocked = false;
    pageLimit = Number($("page-size").value);
    updateMetrics(); render();
  }

  function collectFilters() {
    const filters = {};
    const deputy = selectedFilters.parliamentarian;
    if (deputy) filters.deputy_id = deputy.id.replace(/^deputy:/, "");
    if (selectedFilters.supplier) filters.supplier_id = selectedFilters.supplier.id;
    if ($("year").value) filters.year = $("year").value;
    if ($("date-from").value) filters.date_from = $("date-from").value;
    if ($("date-to").value) filters.date_to = $("date-to").value;
    if ($("query").value.trim()) filters.query = $("query").value.trim();
    return filters;
  }

  function filterDescription() {
    const from = $("date-from").value, to = $("date-to").value;
    const displayDate = (value) => value.split("-").reverse().join("/");
    const range = from && to ? `Emissão: ${displayDate(from)} a ${displayDate(to)}` :
      from ? `Emissão a partir de ${displayDate(from)}` : to ? `Emissão até ${displayDate(to)}` : "";
    return [selectedFilters.parliamentarian?.label, selectedFilters.supplier?.label,
      $("year").value ? `Competência: ${$("year").value}` : "", range,
      $("query").value.trim()].filter(Boolean).join(" · ") || "Todos os registros";
  }

  function validateDates(report = false) {
    const from = $("date-from"), to = $("date-to");
    to.setCustomValidity(from.value && to.value && from.value > to.value ?
      "A data final deve ser igual ou posterior à data inicial." : "");
    const valid = from.checkValidity() && to.checkValidity();
    if (!valid && report) $("filters").reportValidity();
    return valid;
  }

  async function explore() {
    if (!validateDates(true)) return;
    reset();
    activeFilters = collectFilters();
    $("view-label").textContent = filterDescription();
    await loadPage();
  }

  async function loadPage() {
    if (busy || !hasMore || disposed) return;
    if (!validateDates(true)) return;
    // The server binds cursors to page size. Keep it stable through a query.
    if (!cursor) pageLimit = Math.min(pageLimit, store.pageLimit(pageLimit));
    if (store.pageLimit(pageLimit) < pageLimit || pageLimit < 1) {
      budgetBlocked = true;
      setStatus("Limite de pontos atingido. Aumente o limite no diagnóstico ou explore uma pessoa ou empresa específica.");
      updateMetrics(); return;
    }
    busy = true;
    const requestGeneration = generation;
    const controller = new AbortController();
    graphRequest = controller;
    setStatus("Buscando o próximo lote de registros…");
    if (!store.nodes.size && !rendererFailed) overlay("Carregando conexões", "Consultando um pequeno lote de despesas.");
    const params = new URLSearchParams({...activeFilters, limit: pageLimit});
    if (cursor) params.set("cursor", cursor);
    try {
      const page = await request("/api/graph", params, controller, "Registros");
      if (disposed || generation !== requestGeneration) return;
      if (!store.merge(page)) {
        budgetBlocked = true;
        setStatus("O próximo lote excede o limite. Aumente o limite de pontos ou refine a busca.");
        return;
      }
      cursor = page.page.next_cursor;
      hasMore = Boolean(page.page.has_more && cursor);
      drawGraph();
      const counts = store.summary();
      if (!store.nodes.size && !rendererFailed) overlay("Nenhuma conexão encontrada", "Experimente outro ano, nome ou documento.");
      else if (!rendererFailed) overlay(null);
      const total = page.summary?.reimbursements;
      setStatus(`${counts.reimbursement.toLocaleString("pt-BR")} registros carregados${Number.isInteger(total) ? ` de ${total.toLocaleString("pt-BR")} nesta busca` : ""}. ${hasMore ? "Carregue outro lote para continuar." : "Todos os resultados desta busca estão no mapa."}`);
    } catch (error) {
      if (error.name === "AbortError" || requestGeneration !== generation || disposed) return;
      if (!store.nodes.size && !rendererFailed) overlay("Não foi possível carregar", "Revise os filtros e tente novamente.");
      setStatus(error.message || "Falha ao consultar o mapa.");
    } finally {
      if (requestGeneration === generation && !disposed) {
        busy = false; graphRequest = null;
        setStatus($("status").textContent); updateMetrics();
      }
    }
  }

  function drawEdges() {
    if (!lines) return;
    lines.clear();
    for (const edge of store.edges.values()) {
      const source = views.get(edge.source), target = views.get(edge.target);
      if (!source || !target) continue;
      const active = edge.source === selected || edge.target === selected;
      lines.moveTo(source.x, source.y).bezierCurveTo(
        (source.x + target.x) / 2, source.y, (source.x + target.x) / 2, target.y, target.x, target.y,
      ).stroke({color: active ? 0x3b7058 : 0xa8b8b0, width: active ? 2 : 1, alpha: active ? 0.85 : 0.4});
    }
  }

  function drawNode(node, graphic) {
    const active = node.id === selected || node.id === hover;
    graphic.clear();
    if (active) graphic.circle(0, 0, 15).fill({color: colors[node.kind], alpha: 0.15});
    if (node.kind === "parliamentarian") graphic.circle(0, 0, 9);
    else if (node.kind === "supplier") graphic.roundRect(-8, -8, 16, 16, 4);
    else graphic.poly([0, -6, 6, 0, 0, 6, -6, 0]);
    graphic.fill(colors[node.kind]).stroke({color: 0xffffff, width: 1.5});
  }

  function syncLabels() {
    if (!app) return;
    const wanted = new Set([selected, hover].filter(Boolean));
    if ($("labels").checked) {
      for (const node of store.nodes.values()) {
        if (wanted.size >= 36) break;
        if (node.kind !== "reimbursement") wanted.add(node.id);
      }
    }
    for (const [id, label] of labels) {
      if (!wanted.has(id)) { label.destroy({texture: true, textureSource: true}); labels.delete(id); }
    }
    for (const id of wanted) {
      const node = store.nodes.get(id), graphic = views.get(id);
      if (!node || !graphic) continue;
      let label = labels.get(id);
      if (!label) {
        const value = node.label.length > 27 ? `${node.label.slice(0, 25)}…` : node.label;
        label = new PIXI.Text({text: value, style: {fontFamily: "system-ui, sans-serif", fontSize: 12, fill: 0x324f40, fontWeight: "500"}});
        label.eventMode = "none";
        label.anchor.set(node.kind === "parliamentarian" ? 1 : 0, 0.5);
        labels.set(id, label); labelLayer.addChild(label);
      }
      // A small fixed texture per label; scale it instead of rebuilding its text.
      const labelScale = Math.min(2.2, 1 / Math.max(world.scale.x, 0.15));
      label.scale.set(labelScale);
      label.position.set(graphic.x + (node.kind === "parliamentarian" ? -15 : 15) * labelScale, graphic.y);
      label.alpha = selected && id !== selected && id !== hover ? 0.55 : 1;
    }
  }

  function drawGraph() {
    const selectedValue = $("node-select").value;
    const options = [new Option("Escolha para consultar os detalhes", "")];
    for (const kind of NODE_KINDS) {
      const group = document.createElement("optgroup");
      group.label = names[kind];
      for (const node of store.nodes.values()) if (node.kind === kind) group.append(new Option(node.label, node.id));
      if (group.children.length) options.push(group);
    }
    $("node-select").replaceChildren(...options);
    $("node-select").value = selectedValue;
    if (!app) return;
    const positions = layeredPositions(store.nodes, store.edges);
    let maxY = 420;
    const transitions = [];
    for (const [id, node] of store.nodes) {
      const position = positions.get(id);
      maxY = Math.max(maxY, position.y);
      let graphic = views.get(id);
      if (!graphic) {
        graphic = new PIXI.Graphics(); graphic.eventMode = "none";
        graphic.position.set(position.x, position.y); nodeLayer.addChild(graphic); views.set(id, graphic);
      }
      drawNode(node, graphic);
      transitions.push({graphic, x: graphic.x, y: graphic.y, target: position});
    }
    graphBounds = {left: -110, top: -60, right: 1050, bottom: maxY + 60};
    if (animation) cancelAnimationFrame(animation);
    fit();
    const animate = !paused && !matchMedia("(prefers-reduced-motion: reduce)").matches;
    const start = performance.now();
    function positionFrame(now) {
      if (disposed) return;
      const progress = animate ? Math.min(1, (now - start) / 240) : 1;
      if (now - lastFrame >= 30 || progress === 1) {
        const ease = 1 - (1 - progress) ** 3;
        for (const item of transitions) item.graphic.position.set(item.x + (item.target.x - item.x) * ease, item.y + (item.target.y - item.y) * ease);
        drawEdges(); syncLabels(); render(); lastFrame = now;
      }
      animation = progress < 1 ? requestAnimationFrame(positionFrame) : 0;
    }
    if (animate) animation = requestAnimationFrame(positionFrame);
    else positionFrame(start + 250);
    updateMetrics();
  }

  function fit() {
    if (!app) return;
    const width = graphBounds.right - graphBounds.left;
    const height = graphBounds.bottom - graphBounds.top;
    const scale = Math.max(0.04, Math.min(1.3, (app.screen.width - 40) / width, (app.screen.height - 70) / height));
    world.scale.set(scale);
    world.position.set((app.screen.width - width * scale) / 2 - graphBounds.left * scale,
      (app.screen.height - height * scale) / 2 - graphBounds.top * scale + 10);
    $("zoom").textContent = `${Math.round(scale * 100)}%`;
    syncLabels(); render();
  }

  function zoom(factor, x = null, y = null) {
    if (!app) return;
    const centerX = x ?? app.screen.width / 2, centerY = y ?? app.screen.height / 2;
    const previous = world.scale.x;
    const next = Math.max(0.04, Math.min(4, previous * factor));
    world.position.set(centerX - (centerX - world.x) * next / previous, centerY - (centerY - world.y) * next / previous);
    world.scale.set(next); $("zoom").textContent = `${Math.round(next * 100)}%`;
    syncLabels(); render();
  }

  const fieldNames = {
    amount: "Valor líquido", document_amount: "Valor do documento", disallowed_amount: "Glosa informada",
    document_id: "Documento", invoice_number: "Número da nota", date: "Data de emissão",
    category: "Tipo de despesa", year: "Ano", month: "Mês", deputy_id: "Código do parlamentar",
    deputy_name: "Parlamentar", supplier_name: "Fornecedor", supplier_tax_id: "CPF/CNPJ",
    supplier_identity_note: "Identificação do fornecedor", uf: "UF", party: "Partido",
    tax_id: "CPF/CNPJ", identity_note: "Identificação do fornecedor",
    source_file: "Arquivo de origem", source_record_number: "Registro na fonte",
  };
  function selectNode(id) {
    const previous = selected; selected = store.nodes.has(id) ? id : null;
    const node = store.nodes.get(selected);
    $("detail-empty").hidden = Boolean(node); $("detail-content").hidden = !node;
    $("node-select").value = selected || "";
    if (node) {
      $("detail-name").textContent = node.label;
      $("detail-subtitle").textContent = node.subtitle || "";
      $("detail-kind").textContent = names[node.kind]; $("detail-kind").dataset.kind = node.kind;
      const rows = [];
      for (const [key, title] of Object.entries(fieldNames)) {
        const value = node.details?.[key];
        const missingExpenseValue = node.kind === "reimbursement" && ["document_id", "disallowed_amount"].includes(key) &&
          (value === null || value === undefined || value === "");
        if (!missingExpenseValue && (value === null || value === undefined || value === "")) continue;
        const div = document.createElement("div"), dt = document.createElement("dt"), dd = document.createElement("dd");
        dt.textContent = title;
        dd.textContent = missingExpenseValue ? (key === "document_id" ? "Sem código de documento" : "Não informado") :
          key === "document_id" && Number(value) <= 0 ? "Sem código de documento" :
          ["amount", "document_amount", "disallowed_amount"].includes(key) ? formatCents(amountToCents(value)) :
          key === "date" && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value.split("-").reverse().join("/") : String(value);
        div.append(dt, dd); rows.push(div);
      }
      $("detail-fields").replaceChildren(...rows);
      const provenance = [];
      const provenanceNames = {
        document_id: "Código original do documento", source_file_id: "Identidade do arquivo",
        source_record_number: "Posição do registro", source_sha256: "SHA-256 do arquivo", record_sha256: "SHA-256 do registro",
      };
      for (const [key, title] of Object.entries(provenanceNames)) {
        const value = node.details?.[key];
        if (value === undefined || value === null) continue;
        const div = document.createElement("div"), dt = document.createElement("dt"), dd = document.createElement("dd");
        dt.textContent = title; dd.textContent = String(value); div.append(dt, dd); provenance.push(div);
      }
      $("provenance-fields").replaceChildren(...provenance);
      $("provenance").hidden = provenance.length === 0; $("provenance").open = false;
      const connections = [...store.edges.values()].filter((edge) => edge.source === id || edge.target === id).length;
      $("detail-connections").textContent = `${connections} conexões presentes neste mapa.`;
      $("expand").hidden = node.kind === "reimbursement";
      $("expand-help").hidden = node.kind === "reimbursement";
    }
    for (const changed of new Set([previous, selected])) {
      if (views.has(changed)) drawNode(store.nodes.get(changed), views.get(changed));
    }
    drawEdges(); syncLabels(); render();
  }

  function installSearch(kind, field) {
    const input = $(field), results = $(`${field}-results`), chip = $(`${field}-selected`);
    let controller = null, timer = null, sequence = 0, items = [], active = -1;
    function close() { results.hidden = true; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); active = -1; }
    function cancel() { clearTimeout(timer); controller?.abort(); sequence += 1; items = []; results.replaceChildren(); close(); }
    function select(item) {
      selectedFilters[kind] = item; input.value = ""; cancel(); renderChip();
      input.focus();
    }
    function renderChip() {
      chip.replaceChildren(); const item = selectedFilters[kind]; chip.hidden = !item;
      if (!item) return;
      const label = document.createElement("span"), button = document.createElement("button");
      label.textContent = item.label; button.type = "button"; button.textContent = "×"; button.setAttribute("aria-label", `Remover ${item.label}`);
      chip.append(label, button);
    }
    function message(text) {
      results.replaceChildren(); const p = document.createElement("div");
      p.className = "graph-suggestion-message"; p.textContent = text; results.append(p);
      results.hidden = false; input.setAttribute("aria-expanded", "true");
    }
    function highlight(index) {
      active = index;
      [...results.querySelectorAll("[role=option]")].forEach((element, position) => {
        element.setAttribute("aria-selected", String(position === active));
        if (position === active) { input.setAttribute("aria-activedescendant", element.id); element.scrollIntoView({block: "nearest"}); }
      });
    }
    async function lookup() {
      const value = input.value.trim();
      if (value.length < 2) { close(); return; }
      if (!validateDates()) { message("Revise o período de emissão antes de buscar."); return; }
      controller?.abort(); controller = new AbortController();
      const requestSequence = ++sequence;
      message("Buscando…");
      const params = new URLSearchParams({kind, q: value, limit: 12});
      if ($("year").value) params.set("year", $("year").value);
      if ($("date-from").value) params.set("date_from", $("date-from").value);
      if ($("date-to").value) params.set("date_to", $("date-to").value);
      try {
        const data = await request("/api/graph/search", params, controller, `Busca de ${names[kind].toLowerCase()}`);
        if (requestSequence !== sequence || disposed) return;
        items = data.items.slice(0, 12); active = -1; results.replaceChildren();
        if (!items.length) { message("Nenhum resultado. Tente outro nome ou código."); return; }
        for (const [index, item] of items.entries()) {
          const option = document.createElement("div"), label = document.createElement("strong"), sub = document.createElement("span");
          option.id = `graph-${field}-option-${index}`; option.className = "graph-suggestion"; option.dataset.index = String(index);
          option.setAttribute("role", "option"); option.setAttribute("aria-selected", "false");
          label.textContent = item.label; sub.textContent = item.subtitle || "";
          option.append(label, sub);
          results.append(option);
        }
        if (data.has_more) {
          const note = document.createElement("div"); note.className = "graph-suggestion-message";
          note.textContent = "Há mais resultados. Digite mais para refinar."; results.append(note);
        }
        results.hidden = false; input.setAttribute("aria-expanded", "true");
      } catch (error) {
        if (error.name !== "AbortError" && requestSequence === sequence && !disposed) message(error.message);
      }
    }
    listen(input, "input", () => { cancel(); if (input.value.trim().length >= 2) timer = setTimeout(lookup, 250); });
    listen(results, "pointerdown", (event) => {
      const option = event.target.closest("[data-index]");
      if (!option) return;
      event.preventDefault();
      const item = items[Number(option.dataset.index)];
      if (item) select(item);
    });
    listen(chip, "click", (event) => {
      if (event.target.closest("button")) { selectedFilters[kind] = null; renderChip(); input.focus(); }
    });
    listen(input, "keydown", (event) => {
      if (event.key === "Escape") { cancel(); return; }
      if (results.hidden || !items.length) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault(); highlight((active + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length);
      } else if (event.key === "Enter" && active >= 0) { event.preventDefault(); select(items[active]); }
    });
    listen(input, "blur", () => { cancel(); });
    const search = {cancel, renderChip, input}; searches.push(search); return search;
  }

  installSearch("parliamentarian", "deputy");
  installSearch("supplier", "supplier");
  listen($("filters"), "submit", (event) => {
    event.preventDefault();
    if (searches.some((search) => search.input.value.trim())) {
      setStatus("Selecione um nome nos resultados da busca ou use o campo «Buscar nos registros».");
      return;
    }
    explore();
  });
  listen($("clear-filters"), "click", () => {
    selectedFilters.parliamentarian = null; selectedFilters.supplier = null;
    for (const search of searches) { search.input.value = ""; search.renderChip(); }
    $("year").value = ""; $("query").value = "";
    $("date-from").value = ""; $("date-to").value = ""; explore();
  });
  listen($("year"), "change", () => { for (const search of searches) search.cancel(); });
  for (const field of ["date-from", "date-to"]) {
    const changed = () => { validateDates(); for (const search of searches) search.cancel(); };
    listen($(field), "input", changed);
    listen($(field), "change", changed);
  }
  listen($("load-more"), "click", loadPage);
  listen($("node-select"), "change", () => selectNode($("node-select").value));
  listen($("expand"), "click", () => {
    const node = store.nodes.get(selected);
    if (!node || node.kind === "reimbursement") return;
    if (!validateDates(true)) return;
    selectedFilters.parliamentarian = null; selectedFilters.supplier = null;
    selectedFilters[node.kind] = {id: node.id, label: node.label, subtitle: node.subtitle, kind: node.kind};
    for (const search of searches) { search.input.value = ""; search.renderChip(); }
    explore();
  });
  listen($("zoom-in"), "click", () => zoom(1.25));
  listen($("zoom-out"), "click", () => zoom(0.8));
  listen($("fit"), "click", fit);
  listen($("labels"), "change", () => { syncLabels(); render(); updateMetrics(); });
  listen($("layout"), "click", drawGraph);
  listen($("pause"), "click", () => {
    paused = !paused; $("pause").textContent = paused ? "Retomar animação" : "Pausar animação";
    $("pause").setAttribute("aria-pressed", String(paused));
    if (paused && animation) { cancelAnimationFrame(animation); animation = 0; }
  });
  listen($("page-size"), "change", () => explore());
  listen($("node-budget"), "change", () => {
    const newBudget = Number($("node-budget").value);
    store.budget = Math.min(1000, newBudget);
    if (store.nodes.size > store.budget) explore();
    else {
      budgetBlocked = store.pageLimit(pageLimit) < pageLimit;
      setStatus(budgetBlocked ? "O próximo lote excede o limite de pontos. Refine a busca ou aumente o limite." : "Limite atualizado. Continue carregando quando desejar.");
      updateMetrics();
    }
  });
  listen($("clear-memory"), "click", () => {
    reset(); logs.length = 0; $("request-log").replaceChildren();
    metrics.requests = 0; metrics.bytes = 0; metrics.latency = 0;
    if (!rendererFailed) overlay("Mapa vazio", "A memória foi liberada. Use Explorar conexões para começar novamente.");
    hasMore = false; setStatus("Pontos, conexões, textos e histórico de requisições liberados."); updateMetrics();
  });
  listen(window, "pagehide", () => {
    disposed = true; graphRequest?.abort();
    for (const search of searches) search.cancel();
    if (metricInterval) clearInterval(metricInterval);
    resizeObserver?.disconnect();
    clearObjects(); store.clear(); lifetime.abort();
    if (world) app?.destroy({removeView: true}, {children: true, texture: true, textureSource: true, context: true});
    // An application still initializing is disposed immediately after init resolves.
    if (world) app = null;
  });
  window.addEventListener("pageshow", (event) => {
    // A page restored from the back/forward cache needs a fresh renderer and requests.
    if (event.persisted && disposed) window.location.reload();
  });

  try {
    if (!window.PIXI) throw new Error("A biblioteca de visualização não foi carregada.");
    app = new PIXI.Application();
    await app.init({width: $("canvas").clientWidth, height: $("canvas").clientHeight,
      preference: ["webgl"], backgroundAlpha: 0, antialias: true, resolution: Math.min(devicePixelRatio || 1, 2),
      autoDensity: true, autoStart: false, sharedTicker: false});
    if (disposed) { app.destroy({removeView: true}, {children: true, texture: true, textureSource: true}); return; }
    $("canvas").append(app.canvas);
    world = new PIXI.Container(); lines = new PIXI.Graphics();
    nodeLayer = new PIXI.Container(); labelLayer = new PIXI.Container();
    app.stage.eventMode = "none"; world.addChild(lines, nodeLayer, labelLayer); app.stage.addChild(world);
    $("renderer-label").textContent = "Mapa interativo";
    let pointer = null;
    function point(event) {
      const box = app.canvas.getBoundingClientRect(); return {x: event.clientX - box.left, y: event.clientY - box.top};
    }
    function hit(location) {
      let best = null, distance = 18;
      for (const [id, graphic] of views) {
        const dx = graphic.x * world.scale.x + world.x - location.x;
        const dy = graphic.y * world.scale.y + world.y - location.y;
        const value = Math.hypot(dx, dy);
        if (value < distance) { distance = value; best = id; }
      }
      return best;
    }
    listen(app.canvas, "pointerdown", (event) => {
      if (event.button !== 0) return;
      const location = point(event); pointer = {...location, startX: location.x, startY: location.y, moved: false};
      app.canvas.setPointerCapture(event.pointerId);
    });
    listen(app.canvas, "pointermove", (event) => {
      const location = point(event);
      if (pointer) {
        const dx = location.x - pointer.x, dy = location.y - pointer.y;
        world.x += dx; world.y += dy; pointer.x = location.x; pointer.y = location.y;
        pointer.moved ||= Math.hypot(location.x - pointer.startX, location.y - pointer.startY) > 4;
        render();
      } else {
        const next = hit(location);
        if (next !== hover) {
          const previous = hover; hover = next;
          for (const id of new Set([previous, hover])) if (views.has(id)) drawNode(store.nodes.get(id), views.get(id));
          $("canvas").style.cursor = next ? "pointer" : "grab";
          syncLabels(); render();
        }
      }
    });
    listen(app.canvas, "pointerup", (event) => { if (pointer && !pointer.moved) selectNode(hit(point(event))); pointer = null; });
    listen(app.canvas, "pointercancel", () => { pointer = null; });
    listen(app.canvas, "pointerleave", () => {
      if (hover && views.has(hover)) { const previous = hover; hover = null; drawNode(store.nodes.get(previous), views.get(previous)); syncLabels(); render(); }
    });
    listen(app.canvas, "wheel", (event) => { event.preventDefault(); const location = point(event); zoom(Math.exp(-event.deltaY * 0.001), location.x, location.y); }, {passive: false});
    listen($("canvas"), "keydown", (event) => {
      const moves = {ArrowLeft: [40, 0], ArrowRight: [-40, 0], ArrowUp: [0, 40], ArrowDown: [0, -40]};
      if (moves[event.key]) { event.preventDefault(); world.x += moves[event.key][0]; world.y += moves[event.key][1]; render(); }
      else if (["+", "=", "-", "0"].includes(event.key)) { event.preventDefault(); if (event.key === "0") fit(); else zoom(event.key === "-" ? 0.8 : 1.25); }
    });
    resizeObserver = new ResizeObserver(() => {
      if (!disposed && app && $("canvas").clientWidth && $("canvas").clientHeight) {
        app.renderer.resize($("canvas").clientWidth, $("canvas").clientHeight); fit();
      }
    });
    resizeObserver.observe($("canvas"));
  } catch (error) {
    rendererFailed = true;
    if (app?.renderer) app.destroy({removeView: true}, {children: true, texture: true, textureSource: true});
    app = null;
    $("renderer-label").textContent = "Modo de lista";
    overlay("Visualização WebGL indisponível", "Os dados continuam acessíveis pela lista de pontos abaixo. Ative a aceleração gráfica do navegador para ver o mapa.");
    for (const id of ["zoom-in", "zoom-out", "fit", "pause", "layout", "labels"]) $(id).disabled = true;
  }
  if (disposed) return;
  metricInterval = setInterval(() => {
    const now = performance.now();
    metrics.fps = Math.round(metrics.frames * 1000 / (now - tickStarted));
    metrics.frames = 0; tickStarted = now;
    if ($("debug").open) updateMetrics();
  }, 1000);
  await explore();
}
