/* chapter 4: the explained worklist -- every proposed artist link, and the argument for it */
(function () {
  const { $, $$, esc, fmt, label, facts } = EKG;

  const chapter = {
    id: "linking",
    title: "Missing artists",
    view: null,
    current: null,
    filter: "all",
    marketChanged: true,
    dirty: true,

    init(stage) {
      this.el = EKG.h(`<section class="chapter" id="ch-linking"><div class="split three">
        <div class="story">
          <div class="eyebrow">Use case 2 · Artist linking</div>
          <h2>A worklist that explains itself.</h2>
          <p class="lede" id="lkLede"></p>
          <div class="segmented" id="lkFilter">
            <button data-f="all" class="on">Strongest evidence</button>
            <button data-f="spelling">Spelling fixed by the graph</button>
            <button data-f="graph">No name at all</button>
          </div>
          <div class="callout good small"><b>Confirmed in the wild:</b> two hours after our first run, an employee hand-linked 8 artists to “Audio Obscura x EXHALE by Amelie Lens”. Four of them were already our proposals.</div>
          <input class="search" id="lkSearch" placeholder="Filter by event or artist…">
          <div class="worklist"><div class="list" id="lkList"></div></div>
        </div>
        <div class="canvas-wrap">
          <div class="cy" id="lkCy"></div>
          <div class="canvas-title" id="lkTitle"></div>
          <div class="canvas-caption"><div id="lkLegend"></div></div>
        </div>
        <div class="story right" id="lkArgument"></div>
      </div></section>`);
      stage.appendChild(this.el);
      this.view = new EKG.GraphView($("#lkCy", this.el));
      $("#lkSearch", this.el).addEventListener("input", () => this.renderList());
      $$("#lkFilter button", this.el).forEach((b) => (b.onclick = () => {
        this.filter = b.dataset.f;
        $$("#lkFilter button", this.el).forEach((x) => x.classList.toggle("on", x === b));
        this.current = null;
        this.renderList();
        if (this.filter !== "graph") {
          const first = this.rows()[0];
          if (first) this.open(first.id);
        }
      }));
    },

    rows() {
      const d = EKG.data();
      const q = $("#lkSearch", this.el).value.trim().toLowerCase();
      return d.linking.rows
        .filter((r) => d.linking.details[r.id])
        .filter((r) => this.filter !== "spelling" || r.arm === "neighbourhood_equal")
        .filter((r) => !q || label(r.event).toLowerCase().includes(q) || label(r.artist).toLowerCase().includes(q))
        .sort((a, b) => b.score - a.score);
    },

    renderList() {
      if (this.filter === "graph") return this.renderGraphOnlyList();
      const rows = this.rows();
      const list = $("#lkList", this.el);
      list.innerHTML = rows.slice(0, 160).map((r) => `<button class="row ${this.current === r.id ? "on" : ""}" data-id="${r.id}">
          <div class="main"><div class="title">${esc(label(r.artist))}</div><div class="sub">→ ${esc(label(r.event))}</div></div>
          <span class="conf-pill">${Math.round(r.conf * 100)}%</span></button>`).join("") || `<p class="muted">No proposals match.</p>`;
      $$(".row", list).forEach((b) => (b.onclick = () => this.open(b.dataset.id)));
    },

    open(id) {
      const d = EKG.data();
      const detail = d.linking.details[id];
      const row = d.linking.rows.find((r) => r.id === id);
      if (!detail || !row) return;
      this.current = id;
      $$("#lkList .row", this.el).forEach((b) => b.classList.toggle("on", b.dataset.id === id));
      const provider = detail.evidence.provider_name;
      const linked = new Set(detail.edges.flatMap((e) => [e.source, e.target]));
      const nodes = detail.nodes.filter((n) => linked.has(n.node_id) || ["target", "proposed", "provider"].includes(n.role)).map((n) => ({
        node_id: n.node_id,
        role: n.role === "target" ? "target" : n.role === "proposed" ? "proposed" : n.role === "rival" ? "rival" : "",
        label: n.role === "provider" ? `ticket listing: “${EKG.short(provider || "", 22)}”` : n.role === "rival" ? `same name: ${EKG.short(label(n.node_id), 18)}` : undefined,
      }));
      this.view.set(nodes, detail.edges, { layout: "cose" });
      const f = facts(row.event) || {};
      $("#lkTitle", this.el).innerHTML = `<b>${esc(label(row.artist))}</b> → ${esc(label(row.event))}<br><span class="muted">${esc(EKG.eventMeta(row.event))}</span>`;
      $("#lkLegend", this.el).innerHTML = EKG.legendHTML(["event", "artist", "venue", "organizer_brand", "external_event", "city"], [["dash", "proposed link"], ["path", "evidence (hover a reason)"]]);
      this.renderArgument(row, detail);
    },

    renderArgument(row, detail) {
      const ev = detail.evidence;
      const cal = ev.calibrated_on || {};
      const nm = ev.neighbourhood_match;
      const f = facts(row.event) || {};
      const signals = Object.keys(detail.signals).filter((s) => s !== "provider");
      const gs = ev.graph_signals || {};
      const resolution = row.arm === "neighbourhood_equal"
        ? `The spelling differs (<b>“${esc(ev.provider_name)}”</b> vs <b>“${esc(label(row.artist))}”</b>), so a plain lookup fails. The graph narrows 1.2M artist rows down to the <b>${fmt(nm?.neighbourhood_size || 300)}</b> nearest this event by random walk, and the names match inside that set.`
        : ev.same_name_rows > 1
          ? `The name matches <b>${ev.same_name_rows}</b> artist rows. The graph picks the one whose history fits this event.`
          : `The name matches <b>exactly one</b> artist row.`;
      const steps = [
        `<div class="step" data-signal="provider" title="Click to pin"><b>The ticket provider lists “${esc(ev.provider_name)}”</b> for this event (Event Engine import).</div>`,
        `<div class="step">${resolution}</div>`,
        signals.length
          ? `<div class="step"><b>The graph backs it up:</b><div class="list" style="margin-top:6px">${signals.map((s) => `<div class="row" data-signal="${s}" style="cursor:default"><div class="main"><div class="title">${esc(EKG.SIGNAL[s]?.name || s)}</div></div><span class="end">${gs[s] ? `${gs[s]}×` : ""}</span></div>`).join("")}</div><div class="muted small" style="margin-top:4px">Click a reason to pin it on the graph, then hover the lit-up nodes. Click again to release.</div></div>`
          : `<div class="step"><b>No extra graph support.</b> The name alone carries this one.</div>`,
        `<div class="step"><b>Checks passed:</b> no tribute or cover wording, not a placeholder like “TBA”, not a common word.</div>`,
        `<div class="step"><b>Confidence ${Math.round(row.conf * 100)}%.</b> That's how often this rule was right on ${fmt(cal.labels)} ${EKG.state.market} events whose artist we already knew (${cal.split === "independent" ? "judged only against links the importer didn't make" : "all labels"}). Anything below 80% never reaches ops.</div>`,
        `<div class="step"><b>Kept apart from the facts.</b> A proposal is stored next to what we know, never over it, so a whole batch can be undone in one step.</div>`,
      ];
      const box = $("#lkArgument", this.el);
      box.innerHTML = `
        <div class="eyebrow">The argument</div>
        ${EKG.eventCard(row.event, { extra: `<div class="meta">proposed: <b style="color:var(--inferred)">${esc(label(row.artist))}</b></div>` })}
        <div class="steps">${steps.join("")}</div>
        <div class="card"><div class="btn-row" style="justify-content:space-between"><h3 style="margin:0">In plain words</h3><button class="btn" id="lkAi">Ask AI</button></div><div class="ai-box" id="lkAiOut" style="margin-top:8px"></div></div>
`;
      this.pinned = null;
      const restore = () => (this.pinned ? this.traceSignal(this.pinned, row, detail) : this.view.clearHighlight());
      $$("[data-signal]", box).forEach((el) => {
        el.style.cursor = "pointer";
        el.onmouseenter = () => { if (!this.pinned) this.traceSignal(el.dataset.signal, row, detail); };
        el.onmouseleave = restore;
        el.onclick = () => {
          this.pinned = this.pinned === el.dataset.signal ? null : el.dataset.signal;
          $$("[data-signal]", box).forEach((x) => x.classList.toggle("pinned", x.dataset.signal === this.pinned));
          restore();
        };
      });
      $("#lkAi", box).onclick = () => this.explain(row, detail, signals);
    },

    traceSignal(signal, row, detail) {
      const edgeIds = new Set();
      const nodeIds = new Set([row.event, row.artist]);
      if (signal === "provider") {
        detail.edges.filter((e) => e.signal === "provider").forEach((e) => { edgeIds.add(e.edge_id); nodeIds.add(e.source); nodeIds.add(e.target); });
      } else {
        (detail.signals[signal] || []).forEach((path) => path.forEach((id) => edgeIds.add(id)));
        detail.edges.filter((e) => edgeIds.has(e.edge_id)).forEach((e) => { nodeIds.add(e.source); nodeIds.add(e.target); });
      }
      const inferred = detail.edges.find((e) => e.inferred);
      if (inferred) edgeIds.add(inferred.edge_id);
      this.view.highlight([...nodeIds], [...edgeIds]);
    },

    async explain(row, detail, signals) {
      const out = $("#lkAiOut", this.el);
      const f = facts(row.event) || {};
      const prompt = `In 2-3 short sentences for a non-technical audience, explain why a knowledge graph proposes that the artist "${label(row.artist)}" performs at the event "${label(row.event)}" (${f.venue || ""}, ${f.city || ""}, ${f.date || ""}). Evidence: the ticket provider's lineup lists "${detail.evidence.provider_name}"; supporting signals: ${signals.map((s) => `${EKG.SIGNAL[s]?.why} (${(detail.evidence.graph_signals || {})[s] || ""} times)`).join("; ") || "none"}; this rule was correct ${Math.round(row.conf * 100)}% of the time on events whose artist was already known. Be concrete and warm, no jargon, no bullet points.`;
      out.textContent = "";
      try {
        await disco.ai.chat(prompt, { system: "You explain data evidence plainly and briefly.", onToken: (t) => (out.textContent += t) });
      } catch (err) {
        out.textContent = err && err.status === 429 ? "The AI is napping (daily limit reached). The steps above say the same thing." : "AI isn't available right now. The steps above are the full explanation.";
      }
    },

    renderGraphOnlyList() {
      const d = EKG.data();
      const q = $("#lkSearch", this.el).value.trim().toLowerCase();
      const ids = Object.keys(d.graph_only || {}).filter((id) => !q || label(id).toLowerCase().includes(q));
      const list = $("#lkList", this.el);
      list.innerHTML = ids.map((id) => {
        const top = d.graph_only[id].predictions[0];
        return `<button class="row ${this.current === id ? "on" : ""}" data-id="${esc(id)}"><div class="main"><div class="title">${esc(label(id))}</div><div class="sub">${esc(EKG.eventMeta(id))}</div></div><span class="conf-pill low">shortlist</span></button>`;
      }).join("") || `<p class="muted">No examples in this market.</p>`;
      $$(".row", list).forEach((b) => (b.onclick = () => this.openGraphOnly(b.dataset.id)));
      if (ids.length && !ids.includes(this.current)) this.openGraphOnly(ids[0]);
    },

    openGraphOnly(id) {
      const d = EKG.data();
      const ex = d.graph_only[id];
      this.current = id;
      this.pinnedRank = null;
      $$("#lkList .row", this.el).forEach((b) => b.classList.toggle("on", b.dataset.id === id));
      const top = new Set(ex.predictions.slice(0, 5).map((p) => p.artist_node_id));
      this.view.set(ex.nodes.map((n) => ({
        node_id: n.node_id,
        role: n.role === "target" ? "target" : top.has(n.node_id) ? "proposed" : "",
      })), ex.edges, { layout: "cose" });
      $("#lkTitle", this.el).innerHTML = `<b>${esc(label(id))}</b><br><span class="muted">${esc(EKG.eventMeta(id))} · no artist, and no lineup from any ticket provider</span>`;
      $("#lkLegend", this.el).innerHTML = EKG.legendHTML(["event", "artist", "venue", "organizer_brand", "series"], [["path", "route (hover a name)"]]);
      const g = d.walk_grid?.grid?.["4|0.15"];
      const box = $("#lkArgument", this.el);
      box.innerHTML = `
        <div class="eyebrow">Structure only</div>
        ${EKG.eventCard(id)}
        <p class="lede">Nobody has told us who plays here, not even a ticket shop. The graph still has something to say: it walks from the event through its <b>series</b>, <b>promoter</b> and <b>venue</b> to the acts who played the earlier nights.</p>
        <div class="rank-list">${ex.predictions.map((p) => `<div class="rank-item in" data-id="${esc(p.artist_node_id)}" style="cursor:pointer"><span class="r">#${p.rank}</span><span>${esc(label(p.artist_node_id))}</span><span class="muted small" style="text-align:right">${fmt(p.linked_events)} shows</span></div>`).join("")}</div>
        <div class="callout warn"><b>A shortlist, not a link.</b> In a blind test on past events, the real act was in a list like this ${g ? `about <b>${Math.round(g.r10 * 100)}%</b> of the time` : "about 1 in 6 times"}. That's far below the 80% bar for a link, so these go to a person as suggestions to check, never straight onto the event.</div>`;
      $$(".rank-item", box).forEach((el) => {
        const p = ex.predictions.find((x) => x.artist_node_id === el.dataset.id);
        const trace = () => {
          const nodes = new Set([id]);
          const edges = [];
          p.paths.forEach((route) => route.forEach((n, i) => {
            nodes.add(n);
            if (i) { const e = this.view.edgeBetween(route[i - 1], n); if (e) edges.push(e.id()); }
          }));
          this.view.highlight([...nodes], edges);
        };
        el.onmouseenter = () => { if (this.pinnedRank !== el.dataset.id) trace(); };
        el.onmouseleave = () => { if (!this.pinnedRank) this.view.clearHighlight(); };
        el.onclick = () => {
          this.pinnedRank = this.pinnedRank === el.dataset.id ? null : el.dataset.id;
          $$(".rank-item", box).forEach((x) => x.classList.toggle("pinned", x.dataset.id === this.pinnedRank));
          if (this.pinnedRank) trace(); else this.view.clearHighlight();
        };
      });
    },

    render() {
      const d = EKG.data();
      const rows = d.linking.rows;
      const events = new Set(rows.map((r) => r.event)).size;
      const spelling = rows.filter((r) => r.arm === "neighbourhood_equal").length;
      const venueBefore = rows.filter((r) => r.venue_before).length;
      $("#lkLede", this.el).innerHTML = `<b>${fmt(rows.length)}</b> proposed artist links on <b>${fmt(events)}</b> upcoming ${d.country} events, each with its reasons. ${spelling ? `<b>${spelling}</b> needed the graph to fix a spelling. ` : ""}<b>${Math.round((100 * venueBefore) / rows.length)}%</b> name an artist who has played that venue before.`;
      this.current = null;
      this.renderList();
      const first = this.rows()[0];
      if (first) this.open(first.id);
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
