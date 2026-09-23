"use strict";

// colour carries only the three types that dominate a neighbourhood; every other type is
// identified by shape and its label, so no fourth hue is spent and none is cycled
const NODE_STYLE = {
  event: { color: "--type-event", shape: "round-rectangle" },
  artist: { color: "--type-artist", shape: "ellipse" },
  venue: { color: "--type-venue", shape: "diamond" },
  external_event: { color: "--type-entity", shape: "cut-rectangle" },
  organizer_brand: { color: "--type-entity", shape: "hexagon" },
  series: { color: "--type-entity", shape: "barrel" },
  genre: { color: "--type-classification", shape: "tag" },
  city: { color: "--type-classification", shape: "triangle" },
  ticket_provider: { color: "--type-classification", shape: "rhomboid" },
  organizer_company: { color: "--type-classification", shape: "pentagon" },
};

const EDGE_LABEL_CEILING = 80;

const state = {
  meta: null,
  data: null,
  cy: null,
  rootId: null,
  reasoning: null,
  prediction: null,
  activeEdge: null,
};

const el = (id) => document.getElementById(id);

function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function colourFor(nodeType) {
  const spec = NODE_STYLE[nodeType];
  return token(spec ? spec.color : "--type-classification");
}

function shapeFor(nodeType) {
  const spec = NODE_STYLE[nodeType];
  return spec ? spec.shape : "ellipse";
}

async function getJSON(path, params) {
  const url = new URL(path, window.location.origin);
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, value);
    }
  });
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || response.statusText);
  }
  return payload;
}

function walkParameters() {
  return {
    hops: el("hops").value,
    direction: el("direction").value,
    min_confidence: el("min-confidence").value,
    max_degree: el("max-degree").value,
    through_classifications: el("through-classifications").checked ? "1" : "0",
  };
}

// --- search -----------------------------------------------------------------

let searchTimer = null;

function scheduleSearch() {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(runSearch, 160);
}

async function runSearch() {
  const query = el("search").value.trim();
  const list = el("results");
  if (!query) {
    list.replaceChildren();
    return;
  }
  try {
    const { results } = await getJSON("/api/search", {
      q: query,
      types: el("search-type").value,
      limit: 25,
    });
    list.replaceChildren(...results.map(renderResult));
    if (!results.length) {
      const empty = document.createElement("li");
      empty.className = "meta";
      empty.textContent = "nothing matched";
      list.append(empty);
    }
  } catch (error) {
    setStatus(error.message);
  }
}

function renderResult(node) {
  const item = document.createElement("li");

  const dot = document.createElement("span");
  dot.className = "dot";
  dot.style.background = colourFor(node.node_type);

  const label = document.createElement("span");
  label.className = "label";
  label.textContent = node.label;

  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = `${node.node_type} · ${node.degree}`;

  item.append(dot, label, meta);
  item.title = node.node_id;
  item.addEventListener("click", () => showNeighbourhood(node.node_id));
  return item;
}

// --- neighbourhood ----------------------------------------------------------

async function showNeighbourhood(nodeId) {
  setStatus("walking…");
  try {
    const data = await getJSON("/api/neighbourhood", {
      node: nodeId,
      ...walkParameters(),
    });
    state.data = data;
    state.rootId = data.root.node_id;
    state.reasoning = null;
    state.prediction = null;
    rememberLocation({ node: nodeId });
    el("reasoning").hidden = true;
    el("prediction").hidden = true;
    el("detail").hidden = false;
    render(data);
    openDetail(nodeId);
  } catch (error) {
    setStatus(error.message);
  }
}

function render(data, options = {}) {
  const maxHops = data.nodes.reduce((most, node) => Math.max(most, node.hops), 0);
  const concentric = options.concentric || ((node) => maxHops - node.data("hops"));
  const elements = [
    ...data.nodes.map((node) => ({ data: { ...node, id: node.node_id } })),
    ...data.edges.map((edge) => ({ data: { ...edge, id: edge.edge_id } })),
  ];

  if (state.cy) {
    state.cy.destroy();
  }
  syncPanelSpace();

  state.cy = cytoscape({
    container: el("graph"),
    elements,
    style: cytoscapeStyle(options.flaggedLabelsOnly ? 0 : data.edges.length, options),
    layout: {
      name: "concentric",
      concentric,
      levelWidth: () => 1,
      minNodeSpacing: options.spacing || 26,
      avoidOverlap: true,
      animate: false,
    },
    wheelSensitivity: 0.2,
  });

  // a small evidence graph would otherwise be fitted to fill the stage at a silly zoom
  if (state.cy.zoom() > (options.maxZoom || 1.6)) {
    state.cy.zoom(options.maxZoom || 1.6);
    state.cy.center();
  }

  state.cy.on("mouseover", "node", (event) => showTooltip(event, nodeTip(event.target.data())));
  state.cy.on("mouseover", "edge", (event) => showTooltip(event, edgeTip(event.target.data())));
  state.cy.on("mouseout", "node, edge", hideTooltip);
  state.cy.on("tap", "node", (event) => {
    if (state.reasoning || state.prediction) {
      showNeighbourhood(event.target.id());
    } else {
      openDetail(event.target.id());
    }
  });
  state.cy.on("tap", "edge[?inferred]", (event) => showInference(event.target.id()));
  state.cy.on("tap", (event) => {
    if (event.target === state.cy) hideTooltip();
  });

  if (options.status) {
    setStatus(options.status);
    return;
  }
  const truncated = data.truncated
    ? ` · showing ${data.nodes.length} of ${data.reached} (raise Max degree or lower Hops to see fewer)`
    : "";
  const proposed = data.edges.filter((edge) => edge.inferred).length;
  setStatus(
    `${data.nodes.length} nodes · ${data.edges.length} edges` +
      (proposed ? ` · ${proposed} inferred (dashed, click one for why)` : "") +
      truncated
  );
}

function cytoscapeStyle(edgeCount, options = {}) {
  return [
    {
      selector: "node",
      style: {
        "background-color": (node) => colourFor(node.data("node_type")),
        shape: (node) => shapeFor(node.data("node_type")),
        width: (node) => (node.data("hops") === 0 ? 26 : 16),
        height: (node) => (node.data("hops") === 0 ? 26 : 16),
        label: "data(label)",
        "font-size": 10,
        color: token("--text-secondary"),
        "text-wrap": "ellipsis",
        "text-max-width": 130,
        "text-margin-y": 4,
        "text-valign": "bottom",
        // a 2px surface ring keeps overlapping marks legible
        "border-width": 2,
        "border-color": token("--plane"),
      },
    },
    {
      selector: "node[hops = 0]",
      style: {
        "border-width": 3,
        "border-color": token("--text-primary"),
        "font-size": 12,
        color: token("--text-primary"),
      },
    },
    {
      selector: "edge",
      style: {
        "curve-style": "bezier",
        width: "mapData(confidence, 0, 1, 1, 3)",
        "line-color": token("--edge"),
        "target-arrow-color": token("--edge"),
        "target-arrow-shape": "triangle",
        "arrow-scale": 0.6,
        opacity: 0.85,
        label: (edge) =>
          options.flaggedLabelsOnly
            ? edge.data("label") || ""
            : edgeCount <= EDGE_LABEL_CEILING
              ? edge.data("label") || predicateText(edge.data("predicate"))
              : "",
        "font-size": 9,
        color: token("--text-muted"),
        "text-rotation": "autorotate",
        "text-background-color": token("--plane"),
        "text-background-opacity": 0.85,
        "text-background-padding": 2,
      },
    },
    {
      selector: "edge[?on_path]",
      style: {
        "line-color": token("--edge-path"),
        "target-arrow-color": token("--edge-path"),
        opacity: 1,
      },
    },
    {
      selector: "edge[signal]",
      style: {
        "line-color": token("--edge-path"),
        "target-arrow-color": token("--edge-path"),
        width: 1.6,
        opacity: 0.9,
      },
    },
    {
      selector: "edge[?inferred]",
      style: {
        "line-style": "dashed",
        "line-dash-pattern": [7, 4],
        "line-color": token("--accent"),
        "target-arrow-color": token("--accent"),
        width: 3,
        opacity: 1,
        label: (edge) => `proposed · ${edge.data("confidence").toFixed(2)}`,
        color: token("--accent"),
        "font-weight": 600,
      },
    },
    {
      selector: "edge[?rival]",
      style: {
        "line-style": "dotted",
        "line-color": token("--text-muted"),
        "target-arrow-shape": "none",
        width: 1.5,
        opacity: 0.7,
      },
    },
    {
      selector: "edge.lit",
      style: {
        "line-color": token("--accent"),
        "target-arrow-color": token("--accent"),
        width: 4,
        opacity: 1,
        "z-index": 10,
      },
    },
    {
      selector: "edge.dim, node.dim",
      style: { opacity: 0.18 },
    },
    {
      selector: 'node[role = "target"]',
      style: {
        width: 32,
        height: 32,
        "border-width": 3,
        "border-color": token("--text-primary"),
        "font-size": 12,
        "font-weight": 600,
        color: token("--text-primary"),
      },
    },
    {
      selector: 'node[role = "proposed"]',
      style: {
        width: 28,
        height: 28,
        "border-width": 3,
        "border-color": token("--accent"),
        "font-size": 12,
        "font-weight": 600,
        color: token("--text-primary"),
      },
    },
    {
      selector: 'node[role = "rival"]',
      style: { opacity: 0.45, "border-style": "dashed", "border-color": token("--text-muted") },
    },
  ];
}

// --- tooltip ----------------------------------------------------------------

function showTooltip(event, html) {
  const tip = el("tooltip");
  tip.innerHTML = html;
  tip.hidden = false;
  const box = event.cy.container().getBoundingClientRect();
  tip.style.left = `${event.renderedPosition.x + 14}px`;
  tip.style.top = `${Math.min(event.renderedPosition.y + 14, box.height - 90)}px`;
}

function hideTooltip() {
  el("tooltip").hidden = true;
}

function escapeHtml(text) {
  const node = document.createElement("span");
  node.textContent = text;
  return node.innerHTML;
}

function nodeTip(node) {
  return `<div class="tip-title">${escapeHtml(node.label)}</div>
    <div class="tip-meta">${escapeHtml(node.node_type)} · hop ${node.hops}
    · degree ${node.degree} · path confidence ${node.confidence}</div>`;
}

// a redirect merges two listings of one event; the bare predicate reads like a claim about
// two different events being alike
function predicateText(predicate) {
  return predicate === "same_as" ? "same_as · duplicate listing" : predicate;
}

function edgeTip(edge) {
  return `<div class="tip-title">${escapeHtml(predicateText(edge.predicate))}</div>
    <div class="tip-meta">${escapeHtml(edge.provenance)} · ${escapeHtml(edge.source_class)}
    · confidence ${edge.confidence}${edge.inferred ? " · inferred, click for why" : ""}</div>`;
}

// --- detail panel -----------------------------------------------------------

function openDetail(nodeId) {
  const data = state.data;
  if (!data) return;
  const node = data.nodes.find((candidate) => candidate.node_id === nodeId);
  if (!node) return;

  el("detail").hidden = false;
  el("detail-title").textContent = node.label;
  el("detail-subtitle").textContent =
    `${node.node_id} · hop ${node.hops} · degree ${node.degree} · path confidence ${node.confidence}`;
  el("detail-explain").textContent =
    node.hops === 0 ? "This is the node you searched for." : node.explain;

  const byId = new Map(data.nodes.map((item) => [item.node_id, item]));
  const rows = data.edges
    .filter((edge) => edge.source === nodeId || edge.target === nodeId)
    .sort((a, b) => b.confidence - a.confidence)
    .map((edge) => {
      const otherId = edge.source === nodeId ? edge.target : edge.source;
      const other = byId.get(otherId);
      const row = document.createElement("tr");
      if (edge.inferred) {
        row.className = "inferred clickable";
        row.title = "open the reasoning for this proposal";
        row.addEventListener("click", () => showInference(edge.edge_id));
      }
      const direction = edge.source === nodeId ? "→" : "←";
      [
        `${direction} ${predicateText(edge.predicate)}`,
        other ? other.label : otherId,
        `${edge.provenance} (${edge.source_class})`,
        edge.confidence.toFixed(2),
      ].forEach((text, index) => {
        const cell = document.createElement("td");
        if (index === 3) cell.className = "num";
        cell.textContent = text;
        row.append(cell);
      });
      return row;
    });

  el("detail-edges").replaceChildren(...rows);

  const predict = el("detail-predict");
  predict.hidden = node.node_type !== "event";
  predict.onclick = () => showPrediction(nodeId);

  const proposals = data.edges.filter(
    (edge) => edge.inferred && (edge.source === nodeId || edge.target === nodeId)
  );
  el("detail-inferred").hidden = !proposals.length;
  el("detail-inferred-list").replaceChildren(
    ...proposals.map((edge) => {
      const item = document.createElement("li");
      const link = document.createElement("a");
      const otherId = edge.source === nodeId ? edge.target : edge.source;
      const other = byId.get(otherId);
      link.textContent = `${edge.confidence.toFixed(2)} · ${edge.predicate} ${other ? other.label : otherId} — why?`;
      link.addEventListener("click", () => showInference(edge.edge_id));
      item.append(link);
      return item;
    })
  );
}


// --- inferences --------------------------------------------------------------

const SIGNAL_TEXT = {
  venue_history: "Played this venue before",
  city_history: "Played this city before",
  brand_history: "Played for this promoter before",
  series_history: "Played this series before",
  cobilled_before: "Shared a bill with another act on this lineup",
  similar_to_cobilled: "Similar (Chartmetric) to another act on this lineup",
  provider: "The provider named them",
};

const ARM_TEXT = {
  unique_name: "unique name",
  graph_ranked: "graph ranked",
  popularity_only: "popularity only",
  neighbourhood_equal: "spelling, near in graph",
  neighbourhood_typo: "typo, near in graph",
  neighbourhood_contains: "name inside, near in graph",
};

const MATCH_TEXT = {
  equal: "identical once case, accents, punctuation and qualifiers such as (NL) are ignored",
  typo: "a near-identical spelling",
  contains: "the artist's full name appears inside the provider's name",
};

// the side panels float over the stage, so the graph is laid out in the space left of them
function syncPanelSpace() {
  const covered = !el("reasoning").hidden || !el("detail").hidden || !el("prediction").hidden;
  document.querySelector(".stage").classList.toggle("with-panel", covered);
  if (state.cy) state.cy.resize();
}

// the address bar always names what is on screen, so any view can be pasted to someone
function rememberLocation(params) {
  const url = new URL(window.location.href);
  url.search = new URLSearchParams(params).toString();
  window.history.replaceState(null, "", url);
}

function restoreLocation() {
  const params = new URLSearchParams(window.location.search);
  if (params.get("predict")) {
    showPrediction(params.get("predict"));
  } else if (params.get("edge")) {
    showInference(params.get("edge"));
  } else if (params.get("node")) {
    showNeighbourhood(params.get("node"));
  }
}

function selectTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.setAttribute("aria-selected", String(tab.dataset.tab === name));
  });
  el("tab-search").hidden = name !== "search";
  el("tab-inferences").hidden = name !== "inferences";
  el("tab-predict").hidden = name !== "predict";
}

function setupInferences(summary) {
  const box = el("inferred-summary");
  if (!summary) {
    el("inferred-count").textContent = "";
    box.innerHTML =
      '<span class="none">No inferred edges loaded. Start the explorer with ' +
      "<code>--inferred out/lineup_proposals_nl.jsonl</code> or <code>--inferred-run RUN_ID</code>.</span>";
    setStatus("search for an event, artist or venue to begin");
    return;
  }
  el("inferred-count").textContent = summary.proposals.toLocaleString();
  const rules = Object.entries(summary.rules)
    .map(([arm, rule]) => {
      const precision = rule.precision == null ? "?" : `${(rule.precision * 100).toFixed(1)}%`;
      const on = rule.calibrated_on
        ? ` on ${rule.calibrated_on.labels.toLocaleString()} ${rule.calibrated_on.split} labels`
        : "";
      return `${escapeHtml(ARM_TEXT[arm] || arm)}: ${rule.proposals.toLocaleString()} at ${precision} measured precision${on}`;
    })
    .join("<br>");
  const runs = Object.keys(summary.runs).map(escapeHtml).join(", ");
  box.innerHTML =
    `<span class="big">${summary.proposals.toLocaleString()} proposed links</span>` +
    `on ${summary.events.toLocaleString()} artist-less upcoming events<br>${rules}<br>` +
    `<span class="none">run ${runs}</span>`;
  selectTab("inferences");
  loadWorklist();
  setStatus("pick a proposal on the left to see why it was made");
}

let worklistTimer = null;

function scheduleWorklist() {
  window.clearTimeout(worklistTimer);
  worklistTimer = window.setTimeout(loadWorklist, 160);
}

async function loadWorklist() {
  try {
    const payload = await getJSON("/api/inferences", {
      q: el("inferred-filter").value.trim(),
      arm: el("inferred-arm").value,
      limit: 300,
    });
    el("inferred-matched").textContent =
      payload.matched === payload.total
        ? `${payload.total.toLocaleString()} proposals, strongest first`
        : `${payload.matched.toLocaleString()} of ${payload.total.toLocaleString()} match`;
    el("inferred-list").replaceChildren(...payload.rows.map(renderWorklistRow));
  } catch (error) {
    setStatus(error.message);
  }
}

function chip(text, kind) {
  const node = document.createElement("span");
  node.className = `chip${kind ? ` ${kind}` : ""}`;
  node.textContent = text;
  return node;
}

function renderWorklistRow(row) {
  const item = document.createElement("li");
  item.dataset.edge = row.edge_id;
  if (row.edge_id === state.activeEdge) item.classList.add("active");

  const top = document.createElement("div");
  top.className = "row-top";
  const conf = document.createElement("span");
  conf.className = "conf";
  conf.textContent = row.confidence.toFixed(2);
  const artist = document.createElement("span");
  artist.className = "artist";
  artist.textContent = row.artist_label;
  top.append(conf, artist);

  const event = document.createElement("div");
  event.className = "event";
  event.textContent = `→ ${row.event_title}`;

  const chips = document.createElement("div");
  chips.className = "chips";
  chips.append(chip(ARM_TEXT[row.arm] || row.arm));
  if (row.same_name_rows > 1) chips.append(chip(`${row.same_name_rows} same-name rows`, "warn"));
  Object.entries(row.signals).forEach(([signal, count]) =>
    chips.append(chip(`${signal.replace(/_/g, " ")} ${count}`, "strong"))
  );
  if (row.tribute_token) chips.append(chip(`tribute: ${row.tribute_token}`, "warn"));

  item.append(top, event, chips);
  item.title = `${row.artist_node_id} → ${row.event_node_id}`;
  item.addEventListener("click", () => showInference(row.edge_id));
  return item;
}

async function showInference(edgeId) {
  setStatus("building the argument…");
  try {
    const data = await getJSON("/api/inference", { edge: edgeId });
    state.reasoning = data;
    state.activeEdge = edgeId;
    rememberLocation({ edge: edgeId });
    document
      .querySelectorAll("#inferred-list li")
      .forEach((li) => li.classList.toggle("active", li.dataset.edge === edgeId));
    el("detail").hidden = true;
    el("prediction").hidden = true;
    state.prediction = null;
    el("reasoning").hidden = false;
    renderReasoning(data);
  } catch (error) {
    setStatus(error.message);
  }
}

const ROLE_RING = { target: 3, proposed: 2, provider: 2, rival: 2, cobilled: 2, evidence: 1 };

function renderReasoning(data) {
  const shown = Object.values(data.signals).reduce((n, paths) => n + paths.length, 0);
  render(
    { nodes: data.nodes, edges: data.edges },
    {
      concentric: (node) => ROLE_RING[node.data("role")] || 1,
      spacing: 40,
      flaggedLabelsOnly: true,
      maxZoom: 1.25,
      status:
        `why ${data.artist.label} → ${data.event.label}: ` +
        (shown
          ? `${shown} supporting path${shown === 1 ? "" : "s"} · hover a signal on the right to trace it`
          : "proposed on the provider's name alone, nothing in the slice contradicts or supports it") +
        " · click a node to open its neighbourhood",
    }
  );
  renderSteps(data);
}

function nodeLink(nodeId, labels) {
  const link = document.createElement("a");
  link.textContent = labels.get(nodeId) || nodeId;
  link.title = nodeId;
  link.addEventListener("click", () => showNeighbourhood(nodeId));
  return link;
}

function pathSentence(signal, path, edgesById, labels, artistId) {
  const edges = path.map((id) => edgesById.get(id)).filter(Boolean);
  const line = document.createElement("div");
  line.className = "path";
  const parts = [];
  const other = (edge) => (edge.source === artistId ? edge.target : edge.source);
  if (signal === "similar_to_cobilled") {
    parts.push("similar to ", nodeLink(other(edges[0]), labels));
  } else if (signal === "cobilled_before") {
    parts.push(nodeLink(edges[0].target, labels), " with ", nodeLink(edges[1].source, labels));
  } else {
    const joiner = { venue_history: " at ", brand_history: " for ", series_history: " in ", city_history: " at " };
    parts.push(nodeLink(edges[0].target, labels), joiner[signal] || " · ", nodeLink(edges[1].target, labels));
    if (signal === "city_history") parts.push(", ", nodeLink(edges[2].target, labels));
  }
  line.append(...parts);
  return line;
}

function highlight(edgeIds) {
  if (!state.cy) return;
  state.cy.elements().removeClass("lit dim");
  if (!edgeIds) return;
  const lit = state.cy.edges().filter((edge) => edgeIds.has(edge.id()));
  state.cy.elements().addClass("dim");
  lit.removeClass("dim").addClass("lit");
  lit.connectedNodes().removeClass("dim");
  state.cy.nodes('[role = "target"], [role = "proposed"]').removeClass("dim");
}

function step(title, ...body) {
  const item = document.createElement("li");
  const heading = document.createElement("span");
  heading.className = "step-title";
  heading.textContent = title;
  item.append(heading, ...body);
  return item;
}

function text(html) {
  const span = document.createElement("div");
  span.innerHTML = html;
  return span;
}

function renderSteps(data) {
  const evidence = data.evidence || {};
  const labels = new Map(data.nodes.map((node) => [node.node_id, node.label]));
  const edgesById = new Map(data.edges.map((edge) => [edge.edge_id, edge]));
  const artistId = data.artist.node_id;

  el("reasoning-title").innerHTML =
    `${escapeHtml(data.artist.label)} <span class="arrow">performs at</span> ${escapeHtml(data.event.label)}`;
  el("reasoning-subtitle").textContent =
    `confidence ${data.edge.confidence.toFixed(2)} · ${ARM_TEXT[evidence.arm] || evidence.arm} · ${data.run_id}`;
  el("open-event").onclick = () => showNeighbourhood(data.event.node_id);
  el("open-artist").onclick = () => showNeighbourhood(artistId);

  const steps = [];

  steps.push(
    step(
      "The provider named them",
      text(
        `Event Engine provider <code>${escapeHtml(evidence.provider_id || "?")}</code> sent ` +
          `<b>'${escapeHtml(evidence.provider_name || "")}'</b> in the lineup of the imported event ` +
          `<code>${escapeHtml(evidence.external_event_id || "?")}</code>, and the event has no artist linked.`
      )
    )
  );

  const rows = evidence.same_name_rows || 1;
  const near = evidence.neighbourhood_match;
  let resolved;
  if (near) {
    resolved = step(
      "Found near the event, spelled differently",
      text(
        `No artist row is called exactly '${escapeHtml(evidence.provider_name || "")}'. ` +
          `A walk from this event (personalized PageRank through its venue, promoter, series ` +
          `and the acts around them) reaches ${near.neighbourhood_size} artists. Among those ` +
          `only, <b>${escapeHtml(evidence.artist_label || "")}</b> matches: ` +
          `${escapeHtml(MATCH_TEXT[near.kind] || near.kind)} ` +
          `(<code>${escapeHtml(near.provider_form)}</code> vs <code>${escapeHtml(near.artist_form)}</code>). ` +
          `It is #${near.walk_rank} of ${near.neighbourhood_size} by walk score` +
          (near.other_matches ? `, and ${near.other_matches} other neighbour(s) also matched.` : ".") +
          " Matched against all 1.2M artist rows the same rule would be unusable; the graph is what makes it safe."
      )
    );
  } else {
    const resolution =
      rows === 1
        ? "Exactly one live artist row carries this name, so there is nothing to disambiguate."
        : `${rows} live artist rows carry this name. The graph chose the one with the most structural support; the others are drawn greyed out.`;
    resolved = step("Resolved to one artist row", text(escapeHtml(resolution)));
  }
  if (data.rivals.length) {
    const table = document.createElement("table");
    table.className = "edge-table";
    table.innerHTML = "<thead><tr><th>Row</th><th class=\"num\">Score</th><th class=\"num\">Other links</th></tr></thead>";
    const body = document.createElement("tbody");
    (evidence.candidates || []).forEach((candidate) => {
      const tr = document.createElement("tr");
      const chosen = candidate.artist_node_id === artistId;
      tr.innerHTML =
        `<td>${chosen ? "✓ " : ""}${escapeHtml(candidate.label)} <code>${escapeHtml(candidate.artist_node_id.slice(7, 15))}</code></td>` +
        `<td class="num">${candidate.graph_score}</td><td class="num">${candidate.linked_events}</td>`;
      body.append(tr);
    });
    table.append(body);
    resolved.append(table);
  }
  steps.push(resolved);

  const signals = Object.entries(data.signals);
  const graphStep = step("What the graph says");
  if (!data.in_slice) {
    graphStep.append(text("This event is not in the loaded slice, so its surroundings cannot be drawn."));
  } else if (!signals.length) {
    graphStep.append(
      text(
        "Nothing in this market's graph connects the artist to this event yet: no shared venue, city, " +
          "promoter, series or co-billed act. A unique name does not need it; the link rests on the " +
          "provider's name and the rule's measured precision below."
      )
    );
  } else {
    const all = new Set(signals.flatMap(([, paths]) => paths.flat()));
    signals.forEach(([signal, paths]) => {
      const box = document.createElement("div");
      box.className = "signal";
      const head = document.createElement("div");
      head.className = "signal-head";
      const full = (evidence.graph_signals || {})[signal] || paths.length;
      head.innerHTML =
        `<span>${escapeHtml(SIGNAL_TEXT[signal] || signal)}</span>` +
        `<span class="num">${full}${full > paths.length ? `, showing ${paths.length}` : ""}</span>`;
      box.append(head, ...paths.map((path) => pathSentence(signal, path, edgesById, labels, artistId)));
      const ids = new Set(paths.flat());
      box.addEventListener("mouseenter", () => highlight(ids));
      box.addEventListener("mouseleave", () => highlight(null));
      graphStep.append(box);
    });
    graphStep.addEventListener("mouseleave", () => highlight(null));
    graphStep.dataset.edges = String(all.size);
  }
  steps.push(graphStep);

  steps.push(
    step(
      "Guards",
      text(
        evidence.tribute_token
          ? `Tribute token <b>'${escapeHtml(evidence.tribute_token)}'</b> found, so confidence is capped at 0.20.`
          : "No tribute or cover token in the name or title, the name is not a placeholder, and it is not a common word."
      )
    )
  );

  const calibration = evidence.calibrated_on || {};
  const precision = evidence.rule_precision == null ? "?" : `${(evidence.rule_precision * 100).toFixed(1)}%`;
  steps.push(
    step(
      "How sure",
      text(
        `<span class="precision">${precision}</span> of the time, this rule picks the artist ` +
          `already linked, measured on ${(calibration.labels || 0).toLocaleString()} ` +
          `${escapeHtml(calibration.split || "")} labelled events in this market ` +
          `(independent = not linked by the importer itself). That precision is the confidence. ` +
          `Regenerate: <code>${escapeHtml(calibration.command || "")}</code>`
      )
    )
  );

  steps.push(
    step(
      "Where it lives",
      text(
        `An inferred <code>performs_at</code> row, <code>source_class = graph_inferred</code>, ` +
          `run <code>${escapeHtml(data.run_id || "")}</code>. It sits beside the source edges, never over them. ` +
          `Rollback: <code>${escapeHtml(data.rollback)}</code>`
      )
    )
  );

  el("reasoning-steps").replaceChildren(...steps);
}


// --- structure-only predictions ------------------------------------------------

const PREDICT_RING = { target: 3, path: 2, predicted: 1, true: 1 };

async function runPredictionSample() {
  const button = el("predict-run");
  button.disabled = true;
  button.textContent = "walking from 300 events… (the first run builds the walk index, ~10s)";
  try {
    const data = await getJSON("/api/predict/examples", { sample: 300 });
    const summary = el("predict-summary");
    summary.hidden = false;
    const pct = (x) => `${(x * 100).toFixed(0)}%`;
    summary.innerHTML =
      `<span class="big">${pct(data.recall_at_1)} ranked #1</span>` +
      `of ${data.artist_pool.toLocaleString()} artists, across ${data.sample} events whose ` +
      `artists were hidden; ${pct(data.recall_at_10)} land in the top 10. ` +
      `For comparison, ranking the venue's acts by how often they played there gets ` +
      `${pct(data.venue_recall_at_1)} and ${pct(data.venue_recall_at_10)}; picking the ` +
      `most-linked artist overall gets 0%. Duplicate listings are left out of the walk.`;
    el("predict-list").replaceChildren(
      ...data.rows.map((row) => {
        const item = document.createElement("li");
        const top = document.createElement("div");
        top.className = "row-top";
        const badge = document.createElement("span");
        badge.className = `rank-badge${row.rank && row.rank <= 10 ? "" : " miss"}`;
        badge.textContent = row.rank ? `#${row.rank.toLocaleString()}` : "not reached";
        badge.title = row.venue_rank
          ? `venue history alone: #${row.venue_rank.toLocaleString()}`
          : "venue history alone: not reached";
        const label = document.createElement("span");
        label.className = "event";
        label.textContent = row.label;
        top.append(badge, label);
        item.append(top);
        item.addEventListener("click", () => showPrediction(row.event_node_id));
        return item;
      })
    );
  } catch (error) {
    setStatus(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Run on a sample of events";
  }
}

async function showPrediction(nodeId) {
  setStatus("walking from the event… (the first walk builds the index, ~10s)");
  try {
    const data = await getJSON("/api/predict", { node: nodeId, hide_known: "1", limit: 10 });
    state.prediction = data;
    state.reasoning = null;
    rememberLocation({ predict: nodeId });
    el("detail").hidden = true;
    el("reasoning").hidden = true;
    el("prediction").hidden = false;
    renderPrediction(data);
  } catch (error) {
    setStatus(error.message);
  }
}

function renderPrediction(data) {
  render(
    { nodes: data.nodes, edges: data.edges },
    {
      concentric: (node) => PREDICT_RING[node.data("role")] || 2,
      spacing: 34,
      flaggedLabelsOnly: true,
      maxZoom: 1.2,
      status:
        `structure-only prediction for ${data.event.label}: hover a ranked artist to trace ` +
        "the paths the walk took · click a node to open its neighbourhood",
    }
  );
  state.cy.nodes('[role = "true"]').style({
    "border-width": 4,
    "border-color": token("--text-primary"),
    "font-weight": 600,
    color: token("--text-primary"),
  });
  state.cy.nodes('[role = "predicted"]').style({ "font-size": 11, color: token("--text-primary") });
  renderPredictionPanel(data);
}

function renderPredictionPanel(data) {
  el("prediction-title").textContent = data.event.label;
  el("prediction-subtitle").textContent =
    `ranked among ${data.artist_pool.toLocaleString()} artists in this slice`;

  const truth = el("prediction-truth");
  if (!data.known) {
    truth.innerHTML =
      "This event has no artist linked, so there is nothing to hide: the ranking below is the " +
      "graph's guess at who plays it.";
  } else {
    const best = data.truth
      .filter((t) => t.rank)
      .sort((a, b) => a.rank - b.rank)[0];
    const names = data.truth.map((t) => escapeHtml(t.label)).join(", ");
    truth.innerHTML = best
      ? `Really playing: <b>${names}</b>. Their link${data.hidden > 1 ? "s were" : " was"} hidden ` +
        `from the walk, and the graph still ranks <b>${escapeHtml(best.label)}</b> ` +
        `<span class="rank">#${best.rank.toLocaleString()}</span> of ${data.artist_pool.toLocaleString()}.`
      : `Really playing: <b>${names}</b>. With their links hidden, the walk does not reach them: ` +
        "nothing else near this event connects to them.";
  }

  const labels = new Map(data.nodes.map((node) => [node.node_id, node.label]));
  const types = new Map(data.nodes.map((node) => [node.node_id, node.node_type]));
  const STEP_NOUN = {
    event: "gig",
    venue: "venue",
    organizer_brand: "promoter",
    series: "series",
    external_event: "import",
    artist: "act",
  };
  const stepText = (id) => {
    const label = (labels.get(id) || id).replace(/^#\d+ /, "");
    return `${STEP_NOUN[types.get(id)] || types.get(id) || ""} ${label}`.trim();
  };
  const topScore = data.predictions.length ? data.predictions[0].score : 1;
  el("prediction-list").replaceChildren(
    ...data.predictions.map((row) => {
      const item = document.createElement("li");
      const rank = document.createElement("span");
      rank.className = "rank";
      rank.textContent = `#${row.rank}`;
      const who = document.createElement("span");
      who.className = `who${row.is_true ? " true" : ""}`;
      who.textContent = `${row.is_true ? "✓ " : ""}${row.label}`;
      who.title = `${row.artist_node_id} · ${row.linked_events} linked events · score ${row.score.toExponential(2)}`;
      const bar = document.createElement("span");
      bar.className = "bar";
      bar.style.width = `${Math.max(6, (row.score / topScore) * 64)}px`;
      item.append(rank, who, bar);
      if (row.paths.length) {
        const route = document.createElement("span");
        route.className = "route";
        route.textContent =
          "via " +
          row.paths[0].nodes.slice(1, -1).map(stepText).join(" → ");
        item.append(route);
      }
      const ids = new Set(row.paths.flatMap((path) => path.edges));
      item.addEventListener("mouseenter", () => {
        highlight(ids);
        if (state.cy) state.cy.getElementById(row.artist_node_id).removeClass("dim");
      });
      item.addEventListener("mouseleave", () => highlight(null));
      item.addEventListener("click", () => showNeighbourhood(row.artist_node_id));
      return item;
    })
  );
}

// --- chrome -----------------------------------------------------------------

function setStatus(text) {
  el("status").textContent = text;
}

function buildLegend(nodeTypes) {
  const list = el("legend-nodes");
  list.replaceChildren(
    ...nodeTypes.map((nodeType) => {
      const item = document.createElement("li");
      const dot = document.createElement("span");
      dot.className = "dot";
      dot.style.background = colourFor(nodeType);
      item.append(dot, document.createTextNode(nodeType));
      return item;
    })
  );
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    window.localStorage.setItem("event-graph-theme", theme);
  } catch (error) {
    // private windows refuse storage; the theme just does not persist
  }
  buildLegend(state.meta ? state.meta.node_types : []);
  if (state.prediction) {
    renderPrediction(state.prediction);
  } else if (state.reasoning) {
    renderReasoning(state.reasoning);
  } else if (state.data) {
    render(state.data);
  }
}

async function init() {
  try {
    const stored = window.localStorage.getItem("event-graph-theme");
    if (stored) document.documentElement.dataset.theme = stored;
  } catch (error) {
    // storage unavailable; fall through to the OS preference
  }

  el("search").addEventListener("input", scheduleSearch);
  el("search-type").addEventListener("change", scheduleSearch);
  el("detail-close").addEventListener("click", () => {
    el("detail").hidden = true;
    syncPanelSpace();
  });
  el("reasoning-close").addEventListener("click", () => {
    el("reasoning").hidden = true;
    syncPanelSpace();
  });
  document.querySelectorAll(".tab").forEach((tab) =>
    tab.addEventListener("click", () => selectTab(tab.dataset.tab))
  );
  el("inferred-filter").addEventListener("input", scheduleWorklist);
  el("inferred-arm").addEventListener("change", loadWorklist);
  el("predict-run").addEventListener("click", runPredictionSample);
  el("prediction-close").addEventListener("click", () => {
    el("prediction").hidden = true;
    syncPanelSpace();
  });
  el("theme-toggle").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme === "dark";
    applyTheme(dark ? "light" : "dark");
  });
  ["hops", "direction", "min-confidence", "max-degree", "through-classifications"].forEach(
    (id) =>
      el(id).addEventListener("change", () => {
        if (state.rootId) showNeighbourhood(state.rootId);
      })
  );

  try {
    state.meta = await getJSON("/api/meta");
  } catch (error) {
    setStatus(`could not reach the explorer API: ${error.message}`);
    return;
  }

  el("search-type").append(
    ...state.meta.node_types.map((nodeType) => {
      const option = document.createElement("option");
      option.value = nodeType;
      option.textContent = nodeType;
      return option;
    })
  );
  buildLegend(state.meta.node_types);

  el("slice-meta").textContent =
    `${state.meta.nodes.toLocaleString()} nodes · ${state.meta.edges.toLocaleString()} edges\n` +
    `${state.meta.slice}\n` +
    `built ${new Date(state.meta.built_at).toLocaleString()}` +
    (state.meta.git_commit ? ` · commit ${state.meta.git_commit}` : "");

  setupInferences(state.meta.inferred);
  restoreLocation();
}

init();
