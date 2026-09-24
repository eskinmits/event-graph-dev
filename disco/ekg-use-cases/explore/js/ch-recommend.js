/* chapter 6: recommendations -- the same walk, started from the artists you like */
(function () {
  const { $, $$, esc, fmt, label, facts, type } = EKG;

  const fold = (s) => String(s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
  const initials = (s) => String(s).replace(/[^\p{L}\p{N} ]/gu, "").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";

  const chapter = {
    id: "recommend",
    title: "Recommend",
    view: null,
    selected: [],
    marketChanged: true,
    dirty: true,

    init(stage) {
      this.el = EKG.h(`<section class="chapter" id="ch-recommend"><div class="split three">
        <div class="story">
          <div class="eyebrow">Use case 4 · Recommendations (what this unlocks next)</div>
          <h2>Tell us who you like.</h2>
          <p class="lede">Start the random walk from <b>artists</b> instead of an event and it finds the acts and upcoming nights that sit closest to them: through shared stages, shared promoters and similar sound.</p>
          <p class="muted">Pick several. We walk from each artist and blend the results in your browser, giving every artist you pick an equal say.</p>
          <input class="search" id="rcSearch" placeholder="Type any artist, e.g. Kendrick Lamar, Froukje…" autocomplete="off">
          <div class="list" id="rcResults"></div>
          <div class="eyebrow" style="margin-top:4px">Your picks</div>
          <div class="artist-grid" id="rcPicked"></div>
          <div class="muted small" style="margin-top:4px">Or try:</div>
          <div class="artist-grid" id="rcSeeds"></div>
          <div class="callout warn small">A prototype, not a product. Recommendations were out of scope for the hackathon and nothing here has been evaluated. The real version would add 45M favourites and 850M event views on the same graph.</div>
        </div>
        <div class="canvas-wrap">
          <div class="cy" id="rcCy"></div>
          <div class="canvas-title" id="rcTitle"></div>
          <div class="canvas-caption"><div id="rcLegend"></div></div>
        </div>
        <div class="story right rec-panel" id="rcPanel"></div>
      </div></section>`);
      stage.appendChild(this.el);
      this.view = new EKG.GraphView($("#rcCy", this.el));
      const input = $("#rcSearch", this.el);
      input.addEventListener("input", () => this.search(input.value));
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") $("#rcResults .row", this.el)?.click(); });
    },

    async search(text) {
      const box = $("#rcResults", this.el);
      const q = text.trim().toLowerCase();
      if (q.length < 2) { box.innerHTML = ""; return; }
      const index = await EKG.loadIndex();
      const hits = index.filter((r) => r.rec && r.l.toLowerCase().includes(q))
        .sort((a, b) => (a.l.toLowerCase().startsWith(q) ? 0 : 1) - (b.l.toLowerCase().startsWith(q) ? 0 : 1) || b.d - a.d)
        .slice(0, 6);
      box.innerHTML = hits.map((r) => `<button class="row" data-id="${esc(r.id)}"><span class="typedot" style="background:var(--t-artist)"></span><div class="main"><div class="title">${esc(r.l)}</div><div class="sub">${fmt(r.d)} connections</div></div><span class="end">add +</span></button>`).join("")
        || `<p class="muted">Not among the ${EKG.state.market} artists this demo covers.</p>`;
      $$(".row", box).forEach((b) => (b.onclick = () => {
        box.innerHTML = "";
        $("#rcSearch", this.el).value = "";
        if (!this.selected.includes(b.dataset.id)) this.selected = [...this.selected, b.dataset.id];
        this.renderSeeds();
        this.compute();
      }));
    },

    async vector(id) {
      const d = EKG.data();
      if (d.recommend[id]) return d.recommend[id];
      const shard = await EKG.loadShard(id);
      return shard && shard.rec[id];
    },

    renderSeeds() {
      const rec = EKG.data().recommend;
      $("#rcPicked", this.el).innerHTML = this.selected.map((id) => `<button class="artist-chip on" data-id="${esc(id)}" title="Remove"><span class="av">${esc(initials(label(id)))}</span>${esc(label(id))} ×</button>`).join("") || `<span class="muted small">Nothing picked yet.</span>`;
      $$("#rcPicked .artist-chip", this.el).forEach((b) => (b.onclick = () => {
        this.selected = this.selected.filter((x) => x !== b.dataset.id);
        this.renderSeeds();
        this.compute();
      }));
      $("#rcSeeds", this.el).innerHTML = Object.keys(rec).filter((id) => rec[id].events.length).map((id) => `<button class="artist-chip ${this.selected.includes(id) ? "on" : ""}" data-id="${esc(id)}"><span class="av">${esc(initials(label(id)))}</span>${esc(label(id))}</button>`).join("");
      $$("#rcSeeds .artist-chip", this.el).forEach((b) => (b.onclick = () => {
        const id = b.dataset.id;
        this.selected = this.selected.includes(id) ? this.selected.filter((x) => x !== id) : [...this.selected, id];
        this.renderSeeds();
        this.compute();
      }));
    },

    async compute() {
      const seeds = this.selected.slice();
      if (!seeds.length) {
        $("#rcPanel", this.el).innerHTML = `<p class="muted">Pick an artist to start the walk.</p>`;
        this.view.cy.elements().remove();
        return;
      }
      const rec = {};
      for (const s of seeds) rec[s] = await this.vector(s);
      if (seeds.join() !== this.selected.join()) return;
      const seedNames = new Set(seeds.map((s) => fold(label(s))));
      const artistScore = new Map();
      const eventScore = new Map();
      const contrib = new Map();
      const own = new Set();
      for (const s of seeds) {
        const full = rec[s];
        if (!full) continue;
        // preset seeds carry longer lists than typed ones; cut both alike so neither dominates
        const v = { artists: full.artists.slice(0, 60), events: full.events.filter(([, , mine]) => !mine).slice(0, 60) };
        // blend by rank, not raw mass: a walk that spreads thinly (a niche act) would otherwise be
        // drowned out by one that concentrates on a few names, and every pick should count the same
        v.artists.forEach(([a], i) => {
          const m = 1 / (10 + i);
          artistScore.set(a, (artistScore.get(a) || 0) + m);
          if (!contrib.has(a) || contrib.get(a)[1] < m) contrib.set(a, [s, m]);
        });
        full.events.forEach(([e, , mine]) => mine && own.add(e));
        v.events.filter(([, , mine]) => !mine).forEach(([e], i) => {
          const m = 1 / (10 + i);
          eventScore.set(e, (eventScore.get(e) || 0) + m);
          if (!contrib.has(e) || contrib.get(e)[1] < m) contrib.set(e, [s, m]);
        });
      }
      const artists = [...artistScore.entries()]
        .filter(([a]) => !seeds.includes(a) && !seedNames.has(fold(label(a))))
        .sort((a, b) => b[1] - a[1]);
      const seenTitles = new Set();
      const events = [...eventScore.entries()]
        .filter(([e]) => !own.has(e) && facts(e)?.upcoming)
        .sort((a, b) => b[1] - a[1])
        .filter(([e]) => {
          const k = fold(label(e));
          if (seenTitles.has(k)) return false;
          seenTitles.add(k);
          return true;
        });
      this.vectors = rec;
      this.paint(artists.slice(0, 8), events.slice(0, 12), contrib, own);
    },

    because(item, contrib) {
      const [seed] = contrib.get(item) || [];
      const path = seed && this.vectors[seed]?.paths[item];
      if (!path) return seed ? `close to <b>${esc(label(seed))}</b> in the graph` : "";
      return path.map((id, i) => (i === 0 || i === path.length - 1 ? `<b>${esc(EKG.short(label(id), 28))}</b>` : esc(EKG.short(label(id), 28)))).join(" → ");
    },

    paint(artists, events, contrib, own) {
      const seeds = this.selected;
      const panel = $("#rcPanel", this.el);
      panel.innerHTML = `
        <div class="eyebrow">Fans of ${seeds.map((s) => esc(label(s))).join(" + ")} might like</div>
        <div class="chips">${artists.map(([a]) => `<span class="chip" data-id="${esc(a)}" data-tip="${esc(this.because(a, contrib))}">${EKG.typeDot("artist")}${esc(label(a))}</span>`).join("")}</div>
        <div class="eyebrow" style="margin-top:6px">Upcoming nights nearby in the graph</div>
        <div class="rec-events">${events.map(([e]) => EKG.eventCard(e, { tall: true, extra: `<div class="because">${this.because(e, contrib)}</div>` })).join("") || `<p class="muted">No upcoming events reached.</p>`}</div>`;
      $$(".chip[data-tip]", panel).forEach((c) => {
        c.onmousemove = (ev) => EKG.showTip(`<div>${c.dataset.tip}</div>`, ev.clientX, ev.clientY);
        c.onmouseleave = EKG.hideTip;
      });

      // the picture: seeds, the top picks, and the routes that connect them
      const d = EKG.data();
      const nodes = new Map();
      const edges = [];
      seeds.forEach((s) => nodes.set(s, { node_id: s, role: "root", hops: 0 }));
      const add = (id, hops) => { if (!nodes.has(id)) nodes.set(id, { node_id: id, hops }); };
      const picks = [...artists.slice(0, 6).map(([a]) => a), ...events.slice(0, 6).map(([e]) => e)];
      for (const item of picks) {
        const [seed] = contrib.get(item) || [];
        const path = seed && this.vectors[seed]?.paths[item];
        add(item, 2);
        if (!path) {
          if (seed) edges.push({ source: seed, target: item, predicate: "walk", confidence: 0.3, label: "near in the walk", tip: `<b>${esc(label(item))}</b><div>reached by the walk from ${esc(label(seed))}</div>` });
          continue;
        }
        path.forEach((id, i) => {
          add(id, 1);
          if (!i) return;
          const prev = path[i - 1];
          const known = (d.adj.get(prev) || []).find((e) => (e.source === prev && e.target === id) || (e.target === prev && e.source === id));
          edges.push(known ? { ...known, on_path: true } : { source: prev, target: id, predicate: "walk", confidence: 0.5, on_path: true });
        });
      }
      this.view.set([...nodes.values()].map((n) => ({ ...n, role: seeds.includes(n.node_id) ? "root" : picks.includes(n.node_id) ? "" : "", cls: picks.includes(n.node_id) || seeds.includes(n.node_id) ? "" : "rival" })), edges, { layout: "cose" });
      $("#rcTitle", this.el).innerHTML = `The routes from <b>${seeds.map((s) => esc(label(s))).join(" + ")}</b> to the top picks · faded nodes are stepping stones`;
      $("#rcLegend", this.el).innerHTML = EKG.legendHTML(["artist", "event", "venue", "organizer_brand"], [["path", "route the walk took"]]);
    },

    render() {
      const rec = EKG.data().recommend;
      const first = Object.keys(rec).find((id) => rec[id].events.length > 20) || Object.keys(rec)[0];
      this.selected = first ? [first] : [];
      this.renderSeeds();
      if (first) this.compute();
    },

    async enter() {
      this.view.resize();
      if (this.dirty) {
        this.dirty = false;
        this.render();
      }
    },
  };

  EKG.register(chapter);
})();
