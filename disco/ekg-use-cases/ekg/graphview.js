/* one cytoscape wrapper for every chapter, so a node looks the same wherever it appears */
(function () {
  const { TYPE, esc } = EKG;

  const STYLE = [
    {
      selector: "node",
      style: {
        "background-color": "data(color)",
        shape: "data(shape)",
        width: "data(size)",
        height: "data(size)",
        label: "data(short)",
        color: "#d9d7cf",
        "font-family": "Inter, sans-serif",
        "font-size": 11,
        "font-weight": 500,
        "text-valign": "bottom",
        "text-margin-y": 5,
        "text-outline-color": "#0b0c10",
        "text-outline-width": 2.5,
        "text-max-width": 130,
        "text-wrap": "ellipsis",
        "border-width": 0,
        "transition-property": "opacity, background-color, width, height, border-width",
        "transition-duration": "0.25s",
        "min-zoomed-font-size": 4,
      },
    },
    { selector: "node[?hideLabel]", style: { label: "" } },
    { selector: "node.root, node.target", style: { "border-width": 4, "border-color": "#ffffff", "font-size": 13, "font-weight": 700, color: "#fff", "z-index": 20 } },
    { selector: "node.proposed", style: { "border-width": 3, "border-color": "#ffd166", "border-style": "dashed", "font-weight": 700, color: "#ffe29a", "z-index": 19 } },
    { selector: "node.true", style: { "border-width": 4, "border-color": "#2fbf71", color: "#bff0d3", "font-weight": 700, "z-index": 19 } },
    { selector: "node.rival", style: { opacity: 0.55 } },
    { selector: "node.junk", style: { "border-width": 3, "border-color": "#e66767", "background-color": "#e66767" } },
    { selector: "node.hl", style: { "border-width": 3, "border-color": "#00b6f0", "z-index": 30, color: "#fff" } },
    { selector: "node.dim", style: { opacity: 0.14, "text-opacity": 0 } },
    { selector: "node.hidden-answer", style: { "background-color": "#20242e", label: "?", color: "#d95926", "font-size": 16, "text-valign": "center", "text-margin-y": 0, "border-width": 2, "border-color": "#d95926", "border-style": "dashed" } },
    {
      selector: "edge",
      style: {
        width: "data(width)",
        "line-color": "#3d4453",
        "curve-style": "bezier",
        opacity: 0.75,
        "target-arrow-shape": "none",
        "transition-property": "opacity, line-color, width",
        "transition-duration": "0.25s",
      },
    },
    { selector: "edge[predicate = 'similar_to']", style: { "line-color": "#5a3c33", opacity: 0.55 } },
    { selector: "edge[predicate = 'same_as']", style: { "line-color": "#6a5e8e", "line-style": "dotted", width: 3 } },
    { selector: "edge.inferred", style: { "line-color": "#ffd166", "line-style": "dashed", "line-dash-pattern": [7, 5], width: 3.5, opacity: 1, "z-index": 25 } },
    { selector: "edge.rival", style: { "line-color": "#6c7080", "line-style": "dotted", width: 1.5, opacity: 0.6 } },
    { selector: "edge.path", style: { "line-color": "#00b6f0", width: 3.5, opacity: 1, "z-index": 24 } },
    { selector: "edge.clash", style: { "line-color": "#e66767", "line-style": "dashed", width: 3, opacity: 1, label: "data(label)", color: "#f4b3b3", "font-size": 11, "text-background-color": "#0b0c10", "text-background-opacity": 0.85, "text-background-padding": 3 } },
    { selector: "edge.labelled", style: { label: "data(label)", "font-size": 10, color: "#b9b7ae", "text-rotation": "autorotate", "text-background-color": "#0b0c10", "text-background-opacity": 0.8, "text-background-padding": 2 } },
    { selector: "edge.junk-edge", style: { "line-color": "#e66767", opacity: 0.28, width: 1 } },
    { selector: "edge.hl", style: { "line-color": "#00b6f0", width: 4, opacity: 1, "z-index": 30 } },
    { selector: "edge.dim", style: { opacity: 0.06 } },
  ];

  function short(text, n = 26) {
    const s = String(text || "");
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  }

  function nodeSize(n, role) {
    if (role === "target" || role === "root") return 46;
    if (role === "proposed" || role === "true") return 38;
    const d = n.d || 1;
    return Math.max(16, Math.min(34, 12 + Math.log2(d + 1) * 2.4));
  }

  class GraphView {
    constructor(container, { onNodeClick, onNodeHover, onEdgeHover, minZoom = 0.2, maxZoom = 3 } = {}) {
      this.container = container;
      this.cy = cytoscape({
        container,
        style: STYLE,
        minZoom,
        maxZoom,
        wheelSensitivity: 0.25,
        boxSelectionEnabled: false,
        autoungrabify: false,
      });
      this.onNodeClick = onNodeClick;
      this.maxFitZoom = 1.35;
      this.cy.on("tap", "node", (e) => this.onNodeClick && this.onNodeClick(e.target.id(), e.target));
      this.cy.on("mouseover", "node", (e) => {
        const p = e.renderedPosition || e.target.renderedPosition();
        const r = container.getBoundingClientRect();
        EKG.showTip(e.target.data("tip") || EKG.nodeTip(e.target.id()), r.left + p.x, r.top + p.y);
        onNodeHover && onNodeHover(e.target.id(), true);
      });
      this.cy.on("mouseout", "node", () => {
        EKG.hideTip();
        onNodeHover && onNodeHover(null, false);
      });
      this.cy.on("mouseover", "edge", (e) => {
        const r = container.getBoundingClientRect();
        const p = e.renderedPosition;
        const edge = e.target.data("raw");
        if (!edge) return;
        EKG.showTip(e.target.data("tip") || EKG.edgeTip(edge), r.left + p.x, r.top + p.y);
        onEdgeHover && onEdgeHover(edge, true);
      });
      this.cy.on("mouseout", "edge", () => {
        EKG.hideTip();
        onEdgeHover && onEdgeHover(null, false);
      });
    }

    /* nodes: [{node_id, role?, hops?, label?, tip?}], edges: raw edge payloads */
    set(nodes, edges, { layout = "cose", root = null, animate = true, fit = true, labels = "auto" } = {}) {
      const bundle = EKG.data();
      const ids = new Set(nodes.map((n) => n.node_id));
      const elements = [];
      for (const n of nodes) {
        const info = EKG.node(n.node_id, bundle);
        const t = TYPE[info.t] || TYPE.city;
        const role = n.role || (n.node_id === root ? "root" : "");
        elements.push({
          group: "nodes",
          data: {
            id: n.node_id,
            color: n.color || t.color,
            shape: t.shape,
            size: n.size || nodeSize(info, role),
            short: n.label != null ? n.label : short(info.l),
            hops: n.hops ?? 1,
            tip: n.tip,
            hideLabel: labels === "none" || (labels === "auto" && nodes.length > 60 && !role && (n.hops ?? 1) > 1),
          },
          classes: [role, n.cls || ""].join(" ").trim(),
        });
      }
      const seen = new Set();
      for (const e of edges) {
        if (!ids.has(e.source) || !ids.has(e.target)) continue;
        const id = e.edge_id || `${e.source}|${e.predicate}|${e.target}`;
        if (seen.has(id)) continue;
        seen.add(id);
        const cls = [];
        if (e.inferred || e.provenance === "graph_inferred") cls.push("inferred");
        if (e.rival) cls.push("rival");
        if (e.on_path) cls.push("path");
        if (e.cls) cls.push(e.cls);
        elements.push({
          group: "edges",
          data: {
            id,
            source: e.source,
            target: e.target,
            predicate: e.predicate,
            width: 1 + 2.2 * (e.confidence ?? 0.6),
            label: e.label || EKG.PREDICATE_NAME[e.predicate] || e.predicate,
            raw: e,
            tip: e.tip,
          },
          classes: cls.join(" "),
        });
      }
      this.cy.elements().remove();
      this.cy.add(elements);
      this.runLayout(layout, root, animate, fit);
    }

    runLayout(layout, root, animate = true, fit = true) {
      const n = this.cy.nodes().length;
      let opts;
      if (layout === "concentric") {
        opts = {
          name: "concentric",
          concentric: (node) => (node.id() === root ? 100 : 10 - (node.data("hops") || 1)),
          levelWidth: () => 1,
          minNodeSpacing: n > 50 ? 6 : 18,
          spacingFactor: n > 50 ? 0.9 : 1.15,
          startAngle: Math.PI * 1.5,
        };
      } else if (layout === "preset") {
        opts = { name: "preset" };
      } else if (layout === "breadthfirst") {
        opts = { name: "breadthfirst", roots: root ? `#${CSS.escape(root)}` : undefined, circle: true, spacingFactor: 1.05 };
      } else {
        opts = {
          name: "cose",
          idealEdgeLength: () => (n > 60 ? 55 : 85),
          nodeRepulsion: () => (n > 60 ? 7000 : 14000),
          gravity: 0.35,
          numIter: 1400,
          nodeDimensionsIncludeLabels: n < 50,
          randomize: true,
          componentSpacing: 30,
        };
      }
      const run = this.cy.layout({ ...opts, animate: animate && n < 160 && layout !== "preset", animationDuration: 600, fit, padding: 64 });
      // a two-node picture fitted to the canvas is all node; cap the zoom so it reads as a graph
      run.one("layoutstop", () => {
        if (fit && this.cy.zoom() > this.maxFitZoom) {
          this.cy.zoom(this.maxFitZoom);
          this.cy.center();
        }
      });
      run.run();
      return run;
    }

    highlight(nodeIds = [], edgeIds = []) {
      const nodes = new Set(nodeIds);
      const edges = new Set(edgeIds);
      this.cy.batch(() => {
        this.cy.elements().removeClass("hl dim");
        if (!nodes.size && !edges.size) return;
        this.cy.nodes().forEach((n) => (nodes.has(n.id()) ? n.addClass("hl") : n.addClass("dim")));
        this.cy.edges().forEach((e) => (edges.has(e.id()) ? e.addClass("hl") : e.addClass("dim")));
      });
    }

    clearHighlight() { this.cy.elements().removeClass("hl dim"); }

    edgeBetween(a, b) {
      return this.cy.edges().filter((e) => (e.source().id() === a && e.target().id() === b) || (e.source().id() === b && e.target().id() === a))[0];
    }

    resize() { this.cy.resize(); }
    fit(pad = 50) { this.cy.fit(undefined, pad); }
  }

  EKG.GraphView = GraphView;
  EKG.short = short;

  EKG.legendHTML = function (types, extras = []) {
    const t = types.map((k) => `<span>${EKG.typeDot(k)}${esc(TYPE[k].name)}</span>`).join("");
    const x = extras.map(([cls, text]) => `<span><i class="line ${cls}"></i>${esc(text)}</span>`).join("");
    return `<div class="legend legend-panel">${t}${x}</div>`;
  };
})();
