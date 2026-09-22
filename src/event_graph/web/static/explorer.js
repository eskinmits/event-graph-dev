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
    render(data);
    openDetail(nodeId);
  } catch (error) {
    setStatus(error.message);
  }
}

function render(data) {
  const maxHops = data.nodes.reduce((most, node) => Math.max(most, node.hops), 0);
  const elements = [
    ...data.nodes.map((node) => ({ data: { ...node, id: node.node_id } })),
    ...data.edges.map((edge) => ({ data: { ...edge, id: edge.edge_id } })),
  ];

  if (state.cy) {
    state.cy.destroy();
  }

  state.cy = cytoscape({
    container: el("graph"),
    elements,
    style: cytoscapeStyle(data.edges.length),
    layout: {
      name: "concentric",
      concentric: (node) => maxHops - node.data("hops"),
      levelWidth: () => 1,
      minNodeSpacing: 26,
      avoidOverlap: true,
      animate: false,
    },
    wheelSensitivity: 0.2,
  });

  state.cy.on("mouseover", "node", (event) => showTooltip(event, nodeTip(event.target.data())));
  state.cy.on("mouseover", "edge", (event) => showTooltip(event, edgeTip(event.target.data())));
  state.cy.on("mouseout", "node, edge", hideTooltip);
  state.cy.on("tap", "node", (event) => openDetail(event.target.id()));
  state.cy.on("tap", (event) => {
    if (event.target === state.cy) hideTooltip();
  });

  const truncated = data.truncated
    ? ` · showing ${data.nodes.length} of ${data.reached} (raise Max degree or lower Hops to see fewer)`
    : "";
  setStatus(`${data.nodes.length} nodes · ${data.edges.length} edges${truncated}`);
}

function cytoscapeStyle(edgeCount) {
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
        label: edgeCount <= EDGE_LABEL_CEILING ? "data(predicate)" : "",
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
      selector: "edge[?inferred]",
      style: { "line-style": "dashed" },
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

function edgeTip(edge) {
  return `<div class="tip-title">${escapeHtml(edge.predicate)}</div>
    <div class="tip-meta">${escapeHtml(edge.provenance)} · ${escapeHtml(edge.source_class)}
    · confidence ${edge.confidence}${edge.inferred ? " · inferred" : ""}</div>`;
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
      if (edge.inferred) row.className = "inferred";
      const direction = edge.source === nodeId ? "→" : "←";
      [
        `${direction} ${edge.predicate}`,
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
  if (state.data) render(state.data);
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

  setStatus("search for an event, artist or venue to begin");
}

init();
