/* chapter 5: who plays here? a random walk from the event, drawn as walkers you can watch and tune */
(function () {
  const { $, $$, esc, fmt, pct, label } = EKG;

  const WALKERS = 70;
  const HOP_MS = 420;
  const STEP_WORDS = {
    3: "Three steps reach exactly one layer of artists: the acts who played <b>this venue</b> or worked with <b>this promoter</b>.",
    4: "A fourth step adds acts who <b>sound like</b> or <b>shared a bill</b> with those regulars.",
    5: "Five steps wander into the promoter's <b>other venues</b> and the regulars' other stages. More candidates, more noise.",
    6: "Six steps reach far across the scene. Popular acts start to collect mass just for being well connected.",
  };

  const chapter = {
    id: "predict",
    title: "Predict",
    view: null,
    eventId: null,
    steps: 4,
    alpha: 0.15,
    anim: null,
    marketChanged: true,
    dirty: true,

    init(stage) {
      this.el = EKG.h(`<section class="chapter" id="ch-predict"><div class="split three">
        <div class="story">
          <div class="eyebrow">Use case 3 · Artist prediction</div>
          <h2>Hide the artist. Can the graph guess it?</h2>
          <p class="lede">We hide an event's artist and let <b>random walkers</b> wander the graph from the event: to its venue, its promoter, other nights there, the acts who played them. Now and then a walker <b>jumps back home</b> and starts again. The artists they visit most are the guesses.</p>
          <p class="muted">This is <b>personalized PageRank</b>, the idea behind early Google search, run from one event. No names are used, only structure.</p>
          <div class="card">
            <h3>Tune the walk</h3>
            <div class="muted small" style="margin:8px 0 4px">How far may a walker go? <b>Walk length</b></div>
            <div class="segmented" id="prSteps"></div>
            <div class="muted small" style="margin:12px 0 4px">How often does it jump home? <b>Restart chance</b></div>
            <div class="segmented" id="prAlpha"></div>
            <p class="muted small" id="prKnobText" style="margin-top:10px"></p>
          </div>
          <div class="list" id="prExamples" style="max-height:30vh;overflow-y:auto"></div>
        </div>
        <div class="canvas-wrap">
          <div class="cy" id="prCy"></div>
          <canvas class="overlay-canvas" id="prCanvas"></canvas>
          <div class="canvas-title" id="prTitle"></div>
          <div class="canvas-caption"><div id="prLegend"></div><div class="btn-row" style="pointer-events:auto">
            <button class="btn primary" id="prWalk">▶ Start the walk</button>
            <button class="btn" id="prReveal">Reveal the real artist</button>
          </div></div>
        </div>
        <div class="story right">
          <div class="eyebrow">The graph's guesses</div>
          <div class="mystery" id="prMystery"></div>
          <div class="rank-list" id="prRanks"></div>
          <div class="card" id="prEval"></div>
        </div>
      </div></section>`);
      stage.appendChild(this.el);
      this.view = new EKG.GraphView($("#prCy", this.el));
      $("#prWalk", this.el).onclick = () => (this.anim ? this.stop() : this.walk());
      $("#prReveal", this.el).onclick = () => this.reveal();
      window.addEventListener("resize", () => this.sizeCanvas());
    },

    grid() { return EKG.data().walk_grid; },

    renderKnobs() {
      const g = this.grid();
      $("#prSteps", this.el).innerHTML = g.steps.map((s) => `<button data-s="${s}" class="${s === this.steps ? "on" : ""}">${s} steps</button>`).join("");
      $("#prAlpha", this.el).innerHTML = g.alphas.map((a) => `<button data-a="${a}" class="${a === this.alpha ? "on" : ""}">${Math.round(a * 100)}%</button>`).join("");
      $$("#prSteps button", this.el).forEach((b) => (b.onclick = () => { this.steps = +b.dataset.s; this.renderKnobs(); this.renderCombo(); }));
      $$("#prAlpha button", this.el).forEach((b) => (b.onclick = () => { this.alpha = +b.dataset.a; this.renderKnobs(); this.renderCombo(); }));
      const jump = this.alpha <= 0.05 ? "Walkers rarely come home, so they drift far from the event." : this.alpha >= 0.5 ? "Walkers come home every other step, so they stay close to the event." : "Walkers mostly explore, and come home often enough to stay relevant.";
      $("#prKnobText", this.el).innerHTML = `${STEP_WORDS[this.steps] || ""} ${jump}`;
    },

    renderExamples() {
      const d = EKG.data();
      $("#prExamples", this.el).innerHTML = d.predict.order.map((id) => `<button class="row" data-id="${esc(id)}"><div class="main"><div class="title">${esc(label(id))}</div><div class="sub">${esc(EKG.eventMeta(id))}</div></div></button>`).join("");
      $$("#prExamples .row", this.el).forEach((b) => (b.onclick = () => this.open(b.dataset.id)));
    },

    open(id) {
      this.eventId = id;
      $$("#prExamples .row", this.el).forEach((b) => b.classList.toggle("on", b.dataset.id === id));
      this.renderCombo();
    },

    combo() { return this.grid().examples[this.eventId][`${this.steps}|${this.alpha}`]; },

    renderCombo() {
      this.stop(false);
      const id = this.eventId;
      const c = this.combo();
      const edges = c.edges.map(([source, target, predicate, provenance, confidence]) => ({ source, target, predicate, provenance, confidence }));
      const adj = new Map();
      edges.forEach((e) => {
        if (!adj.has(e.source)) adj.set(e.source, []);
        if (!adj.has(e.target)) adj.set(e.target, []);
        adj.get(e.source).push(e.target);
        adj.get(e.target).push(e.source);
      });
      const hops = new Map([[id, 0]]);
      const queue = [id];
      while (queue.length) {
        const cur = queue.shift();
        for (const nb of adj.get(cur) || []) if (!hops.has(nb)) { hops.set(nb, hops.get(cur) + 1); queue.push(nb); }
      }
      this.adj = adj;
      const truth = new Set(Object.keys(c.truth));
      const nodes = c.nodes.map((n) => ({
        node_id: n,
        role: n === id ? "target" : "",
        cls: truth.has(n) ? "hidden-answer" : "",
        tip: truth.has(n) ? "<b>Hidden</b><div>This is the real artist. The walk can't use its link to the event.</div>" : undefined,
        hops: hops.get(n) ?? this.steps,
        label: n === id ? EKG.short(label(n), 30) : undefined,
      }));
      this.view.set(nodes, edges, { layout: "concentric", root: id, animate: false });
      this.baseSize = {};
      this.view.cy.nodes().forEach((n) => (this.baseSize[n.id()] = n.data("size")));
      const maxHop = Math.max(...hops.values());
      $("#prTitle", this.el).innerHTML = `<b>${esc(label(id))}</b> · ${esc(EKG.eventMeta(id))}<br><span class="muted">Each ring is one step from the event. This walk reached <b>${fmt(c.reached)}</b> artists; shown are the routes to its top 10${(c.far || []).length ? " and to a few only a longer walk reaches" : ""}, up to ${maxHop} steps out.</span>`;
      $("#prLegend", this.el).innerHTML = EKG.legendHTML(["event", "artist", "venue", "organizer_brand", "series"]);
      $("#prMystery", this.el).className = "mystery";
      $("#prMystery", this.el).innerHTML = `<span class="q">?</span><span>The real artist is hidden. The walk can't use its link to this event.</span>`;
      const topScore = c.top[0] ? c.top[0][1] : 1;
      $("#prRanks", this.el).innerHTML = c.top.map(([a, score], i) => `<div class="rank-item in" data-id="${esc(a)}"><span class="r">#${i + 1}</span><span>${esc(label(a))}</span><span class="meter"><i style="width:${(100 * score) / topScore}%"></i></span></div>`).join("");
      const far = c.far || [];
      if (far.length) {
        $("#prRanks", this.el).insertAdjacentHTML("beforeend", `<div class="muted small" style="margin-top:8px">Only a longer walk reaches these (drawn on the outer rings):</div>` + far.map(([a, , routes]) => `<div class="rank-item in far" data-id="${esc(a)}"><span class="r">${routes[0].length - 1}↗</span><span>${esc(label(a))}</span><span class="muted small" style="text-align:right">${routes[0].length - 1} steps</span></div>`).join(""));
      }
      $$("#prRanks .rank-item", this.el).forEach((el) => {
        el.onmouseenter = () => this.showRoutes(el.dataset.id);
        el.onmouseleave = () => this.view.clearHighlight();
      });
      $("#prWalk", this.el).textContent = "▶ Start the walk";
      this.renderEval();
      this.sizeCanvas();
    },

    sizeCanvas() {
      const cv = $("#prCanvas", this.el);
      const r = cv.parentElement.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      cv.width = r.width * dpr;
      cv.height = r.height * dpr;
      cv.style.width = `${r.width}px`;
      cv.style.height = `${r.height}px`;
      this.ctx = cv.getContext("2d");
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    },

    walk() {
      const cy = this.view.cy;
      const root = this.eventId;
      const alpha = this.alpha;
      const visits = {};
      const walkers = Array.from({ length: WALKERS }, (_, i) => ({ from: root, to: root, t0: performance.now() + i * 50, dur: HOP_MS, jump: false, depth: 0 }));
      const pick = (at) => {
        const nb = this.adj.get(at) || [];
        return nb.length ? nb[Math.floor(Math.random() * nb.length)] : root;
      };
      const pos = (id) => cy.getElementById(id).renderedPosition();
      let lastHeat = 0;
      $("#prWalk", this.el).textContent = "■ Stop";
      const items = $$("#prRanks .rank-item:not(.far)", this.el);
      items.forEach((el) => el.classList.remove("in"));
      const started = performance.now();
      let shown = 0;

      const frame = (now) => {
        const ctx = this.ctx;
        ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
        for (const w of walkers) {
          let p = (now - w.t0) / w.dur;
          if (p < 0) continue;
          if (p >= 1) {
            visits[w.to] = (visits[w.to] || 0) + 1;
            w.from = w.to;
            w.depth = w.to === root ? 0 : w.depth + 1;
            // the walk is cut at its length: a walker that has used every step goes home
            w.jump = Math.random() < alpha || w.depth >= this.steps;
            w.to = w.jump ? root : pick(w.from);
            w.t0 = now;
            w.dur = w.jump ? 260 : HOP_MS * (0.8 + Math.random() * 0.4);
            p = 0;
          }
          const a = pos(w.from);
          const b = pos(w.to);
          const e = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
          ctx.beginPath();
          ctx.fillStyle = w.jump ? "rgba(255,209,102,0.55)" : "rgba(0,182,240,0.95)";
          ctx.shadowColor = w.jump ? "#ffd166" : "#00b6f0";
          ctx.shadowBlur = 12;
          ctx.arc(a.x + (b.x - a.x) * e, a.y + (b.y - a.y) * e, w.jump ? 3 : 4.4, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.shadowBlur = 0;
        if (now - lastHeat > 400) {
          lastHeat = now;
          const max = Math.max(1, ...Object.entries(visits).filter(([id]) => id !== root).map(([, v]) => v));
          cy.batch(() => {
            for (const [id, v] of Object.entries(visits)) {
              if (id === root) continue;
              const n = cy.getElementById(id);
              const size = this.baseSize[id] + 22 * Math.sqrt(v / max);
              if (n.length) n.style({ width: size, height: size });
            }
          });
          const due = Math.min(items.length, Math.floor((now - started - 1800) / 600));
          while (shown < due) items[shown++].classList.add("in");
        }
        this.anim = requestAnimationFrame(frame);
      };
      this.anim = requestAnimationFrame(frame);
    },

    stop(showAll = true) {
      if (this.anim) cancelAnimationFrame(this.anim);
      this.anim = null;
      if (this.ctx) this.ctx.clearRect(0, 0, this.ctx.canvas.width, this.ctx.canvas.height);
      if (showAll) $$("#prRanks .rank-item", this.el).forEach((el) => el.classList.add("in"));
      const walkBtn = $("#prWalk", this.el);
      if (walkBtn) walkBtn.textContent = "▶ Walk again";
    },

    reveal() {
      this.stop();
      const c = this.combo();
      const d = EKG.data();
      const ex = d.predict.examples[this.eventId];
      this.view.cy.nodes(".hidden-answer").removeClass("hidden-answer").addClass("true").forEach((n) => n.data("tip", null));
      const truth = Object.entries(c.truth).map(([a, rank]) => ({ a, rank }));
      $$("#prRanks .rank-item", this.el).forEach((el) => el.classList.toggle("true", truth.some((t) => t.a === el.dataset.id)));
      const best = truth.filter((t) => t.rank).sort((x, y) => x.rank - y.rank)[0];
      const box = $("#prMystery", this.el);
      box.className = "mystery revealed";
      const venue = ex.venue_rank;
      const pool = fmt(d.predict.summary.artist_pool);
      box.innerHTML = best
        ? `<span class="q" style="color:${best.rank <= 10 ? "var(--good)" : "var(--warn)"}">${best.rank <= 10 ? "✓" : "~"}</span><span><b>${esc(label(best.a))}</b> ranked <b>#${fmt(best.rank)}</b> of ${pool} artists.${venue ? ` Ranking this venue's regular acts by bookings puts them #${venue}.` : ""}</span>`
        : `<span class="q" style="color:var(--bad)">✗</span><span><b>${esc(truth.map((t) => label(t.a)).join(", "))}</b>: this walk never reached them.</span>`;
      if (best && best.rank <= 10) this.showRoutes(best.a);
    },

    showRoutes(artist) {
      const c = this.combo();
      const row = c.top.find(([a]) => a === artist) || (c.far || []).find(([a]) => a === artist);
      if (!row || !row[2].length) return;
      const nodes = new Set();
      const edges = [];
      row[2].forEach((route) => route.forEach((id, i) => {
        nodes.add(id);
        if (i) {
          const e = this.view.edgeBetween(route[i - 1], id);
          if (e) edges.push(e.id());
        }
      }));
      this.view.highlight([...nodes], edges);
      EKG.showTip(`<b>Main route to ${esc(label(artist))}</b><div>${row[2][0].map((id) => esc(label(id))).join(" → ")}</div>`, window.innerWidth * 0.52, 90);
      clearTimeout(this.tipTimer);
      this.tipTimer = setTimeout(EKG.hideTip, 2600);
    },

    renderEval() {
      const d = EKG.data();
      const g = this.grid();
      const s = d.predict.summary;
      const cur = g.grid[`${this.steps}|${this.alpha}`];
      const max = Math.max(cur.r10, s.venue_recall_at_10) * 1.25;
      const bar = (k, v, cls) => `<div class="bar-row"><span class="lbl">${k}</span><span class="bar-track"><span class="bar-fill ${cls}" style="width:${(100 * v) / max}%"></span></span><span class="val">${pct(v)}</span></div>`;
      const all = Object.values(g.grid).map((x) => x.r10);
      const lo = Math.min(...all);
      const hi = Math.max(...all);
      const cell = (st, a) => {
        const v = g.grid[`${st}|${a}`].r10;
        const t = hi > lo ? (v - lo) / (hi - lo) : 0.5;
        const on = st === this.steps && a === this.alpha;
        return `<td style="background:rgba(0,182,240,${0.12 + 0.55 * t});${on ? "outline:2px solid #fff;outline-offset:-2px;" : ""}" title="${st} steps, ${Math.round(a * 100)}% restart: ${pct(v)} in top 10">${Math.round(v * 100)}%</td>`;
      };
      const spell = EKG.state.market === "ES" ? "97%" : "90%";
      $("#prEval", this.el).innerHTML = `
        <h3>How good is it, honestly?</h3>
        <p class="muted" style="margin-bottom:10px">${fmt(g.sample)} past ${EKG.state.market} events with their artist hidden, ranked out of ${fmt(s.artist_pool)} artists. These are the settings you picked.</p>
        <div class="bars">
          ${bar("Random walk · #1", cur.r1, "accent")}
          ${bar("Venue's regulars · #1", s.venue_recall_at_1, "muted")}
          ${bar("Random walk · top 10", cur.r10, "accent")}
          ${bar("Venue's regulars · top 10", s.venue_recall_at_10, "muted")}
        </div>
        <div class="muted small" style="margin:14px 0 6px">Real artist in the top 10, for every setting</div>
        <table class="heat"><tr><th></th>${g.alphas.map((a) => `<th>${Math.round(a * 100)}%</th>`).join("")}</tr>
          ${g.steps.map((st) => `<tr><th>${st} steps</th>${g.alphas.map((a) => cell(st, a)).join("")}</tr>`).join("")}</table>
        <p class="muted small" style="margin-top:8px">The knobs barely move the needle: <b>${Math.round(lo * 100)}–${Math.round(hi * 100)}%</b> across all 16 settings. Structure alone runs out of information at about three steps, the venue's own history. Walking further adds noise, not knowledge.</p>
        <div class="callout warn" style="margin-top:12px"><b>We caught our own leak.</b> The first run scored nearly <b>twice</b> as high. The walkers were cheating: a merged duplicate of the event still carried the hidden artist. With duplicates removed, the lift shrank to what you see here.</div>
        <div class="callout good" style="margin-top:10px"><b>Where it pays off:</b> as a shortlist. Matching a misspelled lineup name against just the <b>300</b> artists nearest the event, instead of all 1.2M, gives links that are <b>${spell}</b> correct (<i>Rowwen Heze → Rowwen Hèze</i>, <i>James Hype (UK) → James HYPE</i>).</div>`;
    },

    render() {
      this.renderKnobs();
      this.renderExamples();
      const d = EKG.data();
      const best = (id) => Math.min(...d.predict.examples[id].truth.map((t) => t.rank || 9999));
      const first = d.predict.order.find((id) => best(id) === 1 && (d.predict.examples[id].venue_rank || 99) > 1) || d.predict.order[0];
      this.open(first);
    },

    async enter() {
      this.view.resize();
      this.sizeCanvas();
      if (this.dirty) {
        this.dirty = false;
        this.render();
      }
    },

    leave() { this.stop(); },
  };

  EKG.register(chapter);
})();
