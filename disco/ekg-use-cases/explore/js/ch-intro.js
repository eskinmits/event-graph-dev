/* chapter 1: what a graph is, told with one real event and one real inferred link */
(function () {
  const { $, esc, fmt, label, facts, TYPE } = EKG;

  const GAPS = [
    ["No artist linked", 0.695],
    ["No genre", 0.375],
  ];

  const chapter = {
    id: "intro",
    title: "Graph 101",
    step: 0,
    view: null,
    example: null,
    marketChanged: true,
    dirty: true,

    init(stage) {
      this.el = EKG.h(`<section class="chapter" id="ch-intro"><div class="intro">
        <div class="story">
          <div class="eyebrow">Disco Days 2026 · Event Knowledge Graph</div>
          <h1>Our events know a lot.<br><em>They just don't know it together.</em></h1>
          <p class="lede">Of the <b>329,740</b> upcoming events on TicketSwap, most are missing basic facts. The facts are usually there. They're just spread across other events, venues, promoters and ticket shops.</p>
          <div class="gap-bars" id="gapBars"></div>
          <div class="card" style="padding:14px 16px">
            <div class="tour-step-text" id="tourText"></div>
            <div class="btn-row" style="justify-content:space-between;margin-top:10px">
              <div class="tour-dots" id="tourDots"></div>
              <div class="btn-row">
                <button class="btn ghost" id="tourBack">Back</button>
                <button class="btn primary" id="tourNext">Show me →</button>
              </div>
            </div>
          </div>
          <p class="muted small">Measured on the TicketSwap event catalogue, 22 Sep 2026.</p>
        </div>
        <div class="canvas-wrap">
          <div class="cy" id="introCy"></div>
          <div class="canvas-title" id="introTitle"></div>
          <div class="canvas-caption"><div id="introLegend"></div><div class="ontology legend-panel legend" id="introScale"></div></div>
        </div>
      </div></section>`);
      stage.appendChild(this.el);
      this.view = new EKG.GraphView($("#introCy", this.el));
      $("#tourNext", this.el).onclick = () => this.go(this.step + 1);
      $("#tourBack", this.el).onclick = () => this.go(this.step - 1);
      $("#gapBars", this.el).innerHTML = GAPS.map(([k, v]) => `<div class="gap-bar"><span>${k}</span><span class="pct">${(v * 100).toFixed(1)}%</span><div class="track"><div class="fill" data-w="${v}"></div></div></div>`).join("");
    },

    pickExample() {
      const d = EKG.data();
      const rows = Object.fromEntries(d.linking.rows.map((r) => [r.id, r]));
      const details = Object.entries(d.linking.details);
      const good = details.filter(([id, v]) => {
        const sig = Object.keys(v.signals);
        return sig.length >= 4 && v.nodes.length >= 10 && v.nodes.length <= 16 && v.nodes.some((n) => n.role === "provider") && facts(rows[id].event)?.img;
      });
      const [id, detail] = good[0] || details[0];
      return { id, detail, row: rows[id] };
    },

    steps() {
      const ex = this.example;
      const ev = label(ex.row.event);
      const artist = label(ex.row.artist);
      const f = facts(ex.row.event) || {};
      const prec = Math.round((ex.detail.evidence.rule_precision || ex.row.conf) * 100);
      const signals = Object.keys(ex.detail.signals).filter((s) => s !== "provider").map((s) => EKG.SIGNAL[s]?.why).filter(Boolean);
      return [
        { title: "One event, one row.", text: `<b>${esc(ev)}</b>, ${esc(EKG.dateLabel(f.date))} at ${esc(f.venue || "a venue")}. In our database this is a row in a table, and the <b>artist</b> column is empty.` },
        { title: "Every column is a relationship.", text: `A venue, a city, a promoter: each is a thing in its own right, connected to many other events. In a graph we draw them as <b>nodes</b>, and each relationship as a <b>line</b>.` },
        { title: "Every line says who claimed it.", text: `Hover a line. Each one records <b>where it came from</b> (an employee, the seller, a ticket provider) and <b>how sure we are</b>. Thicker means more confident. The ticket shop's listing for this event names <b>“${esc(ex.detail.evidence.provider_name || artist)}”</b>.` },
        { title: "Neighbours know things.", text: `Walk one or two steps out and you reach other events at the same venue and promoter. <b>Those</b> events do have artists.` },
        { title: "Many weak hints, one confident answer.", text: `The graph proposes <b>${esc(artist)}</b> (dashed): the name is on the ticket listing, and ${signals.slice(0, 3).join(", ")}. This kind of proposal was right <b>${prec}%</b> of the time when we tested it on events whose artist we already knew.` },
      ];
    },

    visibleAt(step) {
      const ex = this.example;
      const d = ex.detail;
      const target = ex.row.event;
      const direct = new Set([target]);
      d.edges.forEach((e) => {
        if (["held_at", "promoted_by", "series_of"].includes(e.predicate)) {
          if (e.source === target) direct.add(e.target);
          if (e.target === target) direct.add(e.source);
        }
      });
      d.edges.forEach((e) => {
        if (e.predicate === "in_city" && direct.has(e.source)) direct.add(e.target);
      });
      const provider = d.nodes.filter((n) => n.role === "provider").map((n) => n.node_id);
      const all = d.nodes.map((n) => n.node_id);
      const sets = [
        [target],
        [...direct],
        [...direct, ...provider],
        all.filter((id) => id !== ex.row.artist),
        all,
      ];
      return new Set(sets[Math.min(step, sets.length - 1)]);
    },

    go(step) {
      const steps = this.steps();
      this.step = Math.max(0, Math.min(steps.length - 1, step));
      const s = steps[this.step];
      $("#tourText", this.el).innerHTML = `<h3>${s.title}</h3><p class="lede">${s.text}</p>`;
      $("#tourDots", this.el).innerHTML = steps.map((_, i) => `<i class="${i === this.step ? "on" : ""}"></i>`).join("");
      $("#tourBack", this.el).disabled = this.step === 0;
      const next = $("#tourNext", this.el);
      next.textContent = this.step === steps.length - 1 ? "Explore the graph →" : "Next →";
      next.onclick = this.step === steps.length - 1 ? () => EKG.go("explore") : () => this.go(this.step + 1);

      const visible = this.visibleAt(this.step);
      const cy = this.view.cy;
      cy.batch(() => {
        cy.nodes().forEach((n) => n.style("display", visible.has(n.id()) ? "element" : "none"));
        cy.edges().forEach((e) => {
          const show = visible.has(e.source().id()) && visible.has(e.target().id());
          e.style("display", show ? "element" : "none");
          e.toggleClass("labelled", show && this.step >= 1 && this.step <= 2);
        });
      });
      const shown = cy.nodes().filter((n) => visible.has(n.id()));
      cy.animate({ fit: { eles: shown, padding: 80 } }, {
        duration: 500,
        complete: () => {
          if (cy.zoom() > 1.3) cy.animate({ zoom: 1.3, center: { eles: shown } }, { duration: 250 });
        },
      });
    },

    render() {
      this.example = this.pickExample();
      const d = this.example.detail;
      const nodes = d.nodes.map((n) => ({
        node_id: n.node_id,
        role: n.role === "target" ? "target" : n.role === "proposed" ? "proposed" : "",
        label: n.role === "provider" ? `ticket listing: “${EKG.short(d.evidence.provider_name || "", 22)}”` : undefined,
      }));
      this.view.set(nodes, d.edges.filter((e) => !e.rival), { layout: "cose", animate: false });
      const bundle = EKG.data();
      const counts = bundle.slice.node_counts;
      $("#introLegend", this.el).innerHTML = EKG.legendHTML(["event", "artist", "venue", "organizer_brand", "external_event", "city"], [["", "known fact"], ["dash", "inferred"]]);
      $("#introScale", this.el).innerHTML = `<span>${bundle.country} graph: <b>${fmt(bundle.slice.nodes)}</b> nodes · <b>${fmt(bundle.slice.edges)}</b> edges</span><span>Platform: <b>7.7M</b> · <b>19.3M</b></span>`;
      $("#introTitle", this.el).innerHTML = `A real event from the <b>${bundle.country}</b> catalogue`;
      setTimeout(() => this.go(0), 30);
    },

    async enter() {
      requestAnimationFrame(() => this.el.querySelectorAll(".gap-bar .fill").forEach((f) => (f.style.width = `${f.dataset.w * 100}%`)));
      this.view.resize();
      if (this.dirty) {
        this.dirty = false;
        this.render();
      }
    },
  };

  EKG.register(chapter);
})();
