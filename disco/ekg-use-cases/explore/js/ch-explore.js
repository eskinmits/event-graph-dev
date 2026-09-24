/* chapter 2: type anything, see its neighbourhood and how each neighbour is connected */
(function () {
  const { $, $$, esc, fmt, label, type, node, facts, TYPE } = EKG;

  const MAX_NEIGHBOURS = 70;
  const TYPE_PRIORITY = { artist: 0, venue: 1, organizer_brand: 2, event: 3, series: 4, external_event: 5, city: 6, ticket_provider: 7, organizer_company: 8, genre: 9 };

  const chapter = {
    id: "explore",
    title: "Explore",
    view: null,
    root: null,
    shownEdges: [],
    marketChanged: true,
    dirty: true,

    init(stage) {
      this.el = EKG.h(`<section class="chapter" id="ch-explore"><div class="split">
        <div class="story">
          <div class="eyebrow">Explore</div>
          <h2>Type an event, artist or venue.</h2>
          <p class="lede">See everything connected to it, one or two steps away, and <b>why</b> each thing is there.</p>
          <input class="search" id="exSearch" placeholder="Search e.g. Awakenings, Paradiso, BLØF…" autocomplete="off">
          <div class="list" id="exResults"></div>
          <div class="chips" id="exSeeds"></div>
          <div id="exDetail"></div>
        </div>
        <div class="canvas-wrap">
          <div class="cy" id="exCy"></div>
          <div class="canvas-title" id="exTitle"></div>
          <div class="canvas-caption"><div id="exLegend"></div></div>
        </div>
      </div></section>`);
      stage.appendChild(this.el);
      this.view = new EKG.GraphView($("#exCy", this.el), {
        onNodeClick: (id) => this.select(id),
      });
      this.view.cy.on("dbltap", "node", (e) => this.open(e.target.id()));
      const input = $("#exSearch", this.el);
      input.addEventListener("input", () => this.search(input.value));
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          const first = $("#exResults .row", this.el);
          if (first) first.click();
        }
      });
    },

    async search(text) {
      const box = $("#exResults", this.el);
      const q = text.trim().toLowerCase();
      if (q.length < 2) {
        box.innerHTML = "";
        return;
      }
      const d = EKG.data();
      const index = await EKG.loadIndex();
      const hits = [];
      const seen = new Set();
      const consider = (id, l, deg) => {
        if (seen.has(id)) return;
        const at = l.toLowerCase().indexOf(q);
        if (at < 0) return;
        seen.add(id);
        hits.push([d.explore[id] ? 0 : 1, at === 0 ? 0 : 1, -(deg || 0), id]);
      };
      Object.keys(d.explore).forEach((id) => consider(id, label(id), node(id).d));
      index.forEach((r) => consider(r.id, r.l, r.d));
      hits.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2]);
      box.innerHTML = hits.slice(0, 8).map(([, , , id]) => {
        const r = d.indexById.get(id);
        const n = d.nodes[id] || (r ? { t: r.t, l: r.l, d: r.d } : node(id));
        const sub = n.t === "event" ? EKG.eventMeta(id) || "Upcoming event" : `${TYPE[n.t]?.name || n.t} · ${fmt(n.d)} connections`;
        return `<button class="row" data-id="${esc(id)}">${EKG.typeDot(n.t)}<div class="main"><div class="title">${esc(n.l)}</div><div class="sub">${esc(sub)}</div></div></button>`;
      }).join("") || `<p class="muted">Nothing in this demo matches. Try one of the suggestions below.</p>`;
      $$(".row", box).forEach((b) => (b.onclick = () => {
        box.innerHTML = "";
        $("#exSearch", this.el).value = "";
        this.open(b.dataset.id);
      }));
    },

    /* the view for a node: its exported walk when it has one, else its known edges */
    async open(id) {
      const d = EKG.data();
      await EKG.loadIndex();
      this.root = id;
      let nodes;
      let edges;
      // the searchable view picks bookings and places before look-alikes, so it wins over the preset
      let seed = d.indexById.get(id) ? null : d.explore[id];
      if (!seed && d.indexById.get(id)) {
        const shard = await EKG.loadShard(id);
        const p = shard && shard.explore[id];
        if (p) {
          seed = {
            nodes: p.n.map(([node_id, hops]) => ({ node_id, hops })),
            edges: p.e.map(([source, target, predicate, provenance, confidence, inferred]) => ({ source, target, predicate, provenance, confidence, inferred: !!inferred })),
            reached: p.r,
            truncated: Math.max(0, p.r - p.n.length),
          };
        } else {
          seed = d.explore[id];
        }
      }
      if (seed) {
        nodes = seed.nodes;
        edges = seed.edges.map((e) => ({ ...e, on_path: false }));
      } else {
        const around = (d.adj.get(id) || []).slice();
        around.sort((a, b) => {
          const oa = a.source === id ? a.target : a.source;
          const ob = b.source === id ? b.target : b.source;
          return (TYPE_PRIORITY[type(oa)] ?? 9) - (TYPE_PRIORITY[type(ob)] ?? 9) || (node(ob).d || 0) - (node(oa).d || 0);
        });
        const keep = new Set([id]);
        const kept = [];
        for (const e of around) {
          const other = e.source === id ? e.target : e.source;
          if (keep.size >= MAX_NEIGHBOURS && !keep.has(other)) continue;
          keep.add(other);
          kept.push(e);
        }
        // close the picture: edges among the neighbours themselves
        const extra = [];
        for (const other of keep) {
          if (other === id) continue;
          for (const e of d.adj.get(other) || []) {
            const far = e.source === other ? e.target : e.source;
            if (far !== id && keep.has(far)) extra.push(e);
          }
        }
        nodes = [...keep].map((n) => ({ node_id: n, hops: n === id ? 0 : 1 }));
        edges = kept.map((e) => ({ ...e, on_path: true })).concat(extra);
      }
      this.shownEdges = edges;
      this.view.set(nodes.map((n) => ({ ...n, role: n.node_id === id ? "root" : "" })), edges, { layout: "concentric", root: id });
      const n = node(id);
      $("#exTitle", this.el).innerHTML = `<b>${esc(n.l)}</b> · ${nodes.length - 1} neighbours shown${seed && seed.truncated ? ` of ${fmt(seed.reached)} reachable in two steps` : ""} · rings are steps away`;
      this.select(id);
    },

    pathTo(target) {
      if (target === this.root) return [];
      const adj = new Map();
      for (const e of this.shownEdges) {
        if (!adj.has(e.source)) adj.set(e.source, []);
        if (!adj.has(e.target)) adj.set(e.target, []);
        adj.get(e.source).push([e.target, e]);
        adj.get(e.target).push([e.source, e]);
      }
      const prev = new Map([[this.root, null]]);
      const queue = [this.root];
      while (queue.length) {
        const cur = queue.shift();
        if (cur === target) break;
        for (const [next, e] of adj.get(cur) || []) {
          if (prev.has(next)) continue;
          prev.set(next, [cur, e]);
          queue.push(next);
        }
      }
      if (!prev.has(target)) return [];
      const path = [];
      let at = target;
      while (prev.get(at)) {
        const [from, e] = prev.get(at);
        path.unshift(e);
        at = from;
      }
      return path;
    },

    select(id) {
      const d = EKG.data();
      const n = node(id);
      const t = TYPE[n.t] || TYPE.city;
      const path = this.pathTo(id);
      const counts = {};
      for (const e of d.adj.get(id) || []) counts[e.predicate] = (counts[e.predicate] || 0) + 1;
      const f = facts(id);
      const why = path.length
        ? `<div class="card"><h3>Why it's here</h3><div class="steps">${path.map((e) => `<div class="step"><b>${esc(label(e.source))}</b> ${esc(EKG.VERB[e.predicate] || e.predicate)} <b>${esc(label(e.target))}</b><div class="muted">${esc(EKG.provenance(e))}${e.confidence != null ? ` · ${Math.round(e.confidence * 100)}% confidence` : ""}</div></div>`).join("")}</div></div>`
        : "";
      $("#exDetail", this.el).innerHTML = `
        <div class="card">
          <div class="tt-type" style="color:${t.color};font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase">${t.name}</div>
          <h3 style="font-size:20px;margin:4px 0 8px">${esc(n.l)}</h3>
          ${f ? EKG.eventCard(id, { tall: true }) : ""}
          <p class="muted" style="margin-top:8px">${fmt(n.d)} connections in the ${EKG.state.market} graph${Object.keys(counts).length ? " · here: " + Object.entries(counts).map(([p, c]) => `${c} ${EKG.PREDICATE_NAME[p] || p}`).join(", ") : ""}</p>
          ${id !== this.root ? `<div class="btn-row" style="margin-top:10px"><button class="btn" id="exRecenter">Center on this →</button></div>` : ""}
        </div>${why}`;
      const recenter = $("#exRecenter", this.el);
      if (recenter) recenter.onclick = () => this.open(id);
      if (path.length) {
        const ids = new Set([this.root, id]);
        const edgeIds = [];
        path.forEach((e) => {
          ids.add(e.source);
          ids.add(e.target);
          const el = this.view.edgeBetween(e.source, e.target);
          if (el) edgeIds.push(el.id());
        });
        this.view.highlight([...ids], edgeIds);
      } else {
        this.view.clearHighlight();
      }
    },

    render() {
      const d = EKG.data();
      $("#exSeeds", this.el).innerHTML = `<span class="muted" style="width:100%">Try:</span>` + Object.keys(d.explore).map((id) => `<button class="chip" data-id="${esc(id)}">${EKG.typeDot(type(id))}${esc(EKG.short(label(id), 30))}</button>`).join("");
      $$("#exSeeds .chip", this.el).forEach((c) => (c.onclick = () => this.open(c.dataset.id)));
      $("#exLegend", this.el).innerHTML = EKG.legendHTML(["event", "artist", "venue", "organizer_brand", "external_event", "series", "city"], [["", "fact (thicker = surer)"], ["dash", "inferred"], ["path", "why it's here"]]);
      this.open(Object.keys(d.explore)[0]);
    },

    async enter() {
      this.view.resize();
      if (this.dirty) {
        this.dirty = false;
        this.render();
      }
    },

    show(id) {
      EKG.go("explore").then(() => this.open(id));
    },
  };

  EKG.explore = chapter;
  EKG.register(chapter);
})();
