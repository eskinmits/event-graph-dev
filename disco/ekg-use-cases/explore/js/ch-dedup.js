/* chapter 3: duplicates -- a game the room plays, the numbers behind it, and what linking reveals */
(function () {
  const { $, $$, esc, fmt, label, node, facts } = EKG;

  const RULES = [
    ["Same venue, same day", 0.163, "base"],
    ["… and exact start time", 0.243],
    ["… and identical title", 0.357],
    ["… exact time + identical title", 0.371],
    ["… and a shared artist", 0.386, "graph"],
  ];

  const LIFTS = [
    ["Shared artist", 3.49, "1 hop", "pos"],
    ["2-hop neighbourhood overlap", 1.05, "fires on 75% of pairs", "neu"],
    ["Both have no artist (control)", 0.95, "", "neu"],
    ["Similar artists, none shared", 0.54, "2 hops", "neg"],
    ["Same promoter", 0.31, "1 hop", "neg"],
  ];

  const SUBS = [
    ["cases", "How the graph spots one"],
    ["why", "Why titles fail"],
    ["clash", "Links reveal duplicates"],
    ["artists", "Same name, same act?"],
  ];

  const chapter = {
    id: "dedup",
    title: "Duplicates",
    sub: "cases",
    caseIndex: 0,
    caseStep: 0,
    view: null,
    marketChanged: true,
    dirty: true,

    init(stage) {
      this.el = EKG.h(`<section class="chapter" id="ch-dedup"><div class="split">
        <div class="story">
          <div class="eyebrow">Use case 1 · Deduplication</div>
          <div class="subnav segmented" id="ddSub"></div>
          <div id="ddBody" style="display:flex;flex-direction:column;gap:16px"></div>
        </div>
        <div class="canvas-wrap">
          <div class="cy" id="ddCy"></div>
          <div class="canvas-title" id="ddTitle"></div>
          <div class="canvas-caption"><div id="ddLegend"></div></div>
        </div>
      </div></section>`);
      stage.appendChild(this.el);
      this.view = new EKG.GraphView($("#ddCy", this.el));
      $("#ddSub", this.el).innerHTML = SUBS.map(([k, v]) => `<button data-sub="${k}">${v}</button>`).join("");
      $$("#ddSub button", this.el).forEach((b) => (b.onclick = () => this.show(b.dataset.sub)));
    },

    show(sub) {
      this.sub = sub;
      $$("#ddSub button", this.el).forEach((b) => b.classList.toggle("on", b.dataset.sub === sub));
      this.view.cy.elements().remove();
      $("#ddTitle", this.el).innerHTML = "";
      ({ cases: () => this.renderCases(), why: () => this.renderWhy(), clash: () => this.renderClash(), artists: () => this.renderArtists() })[sub]();
    },

    /* ---------------- how the graph reads a pair, step by step ---------------- */
    cases() { return EKG.data().duplicate_cases || []; },

    renderCases() {
      const cases = this.cases();
      const body = $("#ddBody", this.el);
      body.innerHTML = `
        <h2>How the graph spots a duplicate.</h2>
        <p class="lede">Two listings at <b>the same venue on the same night</b> with <b>different titles</b>. Is it one show listed twice, or two different shows? Text can't tell. Watch the graph build its answer one fact at a time.</p>
        <div class="card" id="ddCaseCard"></div>
        <div class="eyebrow">More cases</div>
        <div class="list" id="ddCases">${cases.map((c, i) => `<button class="row" data-i="${i}"><span class="typedot" style="background:var(--t-event)"></span><div class="main"><div class="title">${esc(label(c.a))}</div><div class="sub">vs ${esc(label(c.b))}</div></div><span class="end">${esc(EKG.dateLabel(facts(c.a)?.date))}</span></button>`).join("")}</div>`;
      const rows = $$("#ddCases .row", body);
      rows.forEach((row) => (row.onclick = () => {
        rows.forEach((r) => r.classList.toggle("on", r === row));
        this.caseIndex = +row.dataset.i;
        this.caseStep = 0;
        this.drawCase();
      }));
      if (rows[this.caseIndex || 0]) rows[this.caseIndex || 0].click();
    },

    caseSteps(c) {
      const shared = c.shared_artists.map((a) => label(a));
      const brands = c.shared_brands.map((b) => label(b));
      const artistStep = shared.length
        ? { title: "3 · Who's playing?", sig: "pos", text: `<b>${esc(shared.slice(0, 3).join(", "))}</b> ${shared.length > 1 ? "are" : "is"} linked to <b>both</b> listings. One act can't play two separate shows in one room on the same night, so pairs like this turn out to be duplicates <b>3.5×</b> as often.` }
        : { title: "3 · Who's playing?", sig: "neu", text: `No artist is linked to both listings${c.only_a.length || c.only_b.length ? `: each has <b>its own</b> acts` : ""}. The strongest duplicate signal doesn't fire.` };
      const brandStep = brands.length
        ? { title: "4 · Who's organising?", sig: "neg", text: `Both nights are run by <b>${esc(brands.join(", "))}</b>. That sounds like evidence for a duplicate, but it's the opposite: a promoter runs <b>several separate nights</b> at its home venue, so a shared promoter makes a duplicate <b>3× less</b> likely.` }
        : { title: "4 · Who's organising?", sig: "neu", text: "No promoter links the two, so nothing argues for separate nights." };
      const reading = shared.length && !brands.length ? "duplicate" : !shared.length && brands.length ? "different" : "unsure";
      const dup = c.truth === "duplicate";
      return [
        { title: "1 · Two listings, two titles", sig: "neu", text: `“${esc(label(c.a))}” and “${esc(label(c.b))}”. A text matcher compares the strings, sees they differ and stops. <b>38.6%</b> of all the merges ops has done had titles that differed like this.` },
        { title: "2 · Same place, same night", sig: "neu", text: `Both take place at <b>${esc(facts(c.a)?.venue || "the same venue")}</b> on <b>${esc(EKG.dateLabel(facts(c.a)?.date))}</b>. That makes them a candidate pair, but on its own it proves little: only about <b>1 in 6</b> such pairs turns out to be a duplicate. Venues host several events a day.` },
        artistStep,
        brandStep,
        { title: "5 · The graph's reading, and the truth", sig: reading === "duplicate" ? "pos" : reading === "different" ? "neg" : "neu", text: `The graph reads this as <b>${reading === "duplicate" ? "one show listed twice" : reading === "different" ? "two different nights" : "undecided"}</b>. What really happened: ${dup ? "an admin <b>merged</b> these two listings." : "the two listings were <b>never merged</b>, and both are still live as separate events."} ${(reading === "duplicate") === dup && reading !== "unsure" ? "<b style=\"color:var(--good)\">The graph got it right.</b>" : ""}` },
      ];
    },

    drawCase() {
      const c = this.cases()[this.caseIndex];
      if (!c) return;
      const steps = this.caseSteps(c);
      const step = this.caseStep;
      const nodes = [
        { node_id: c.a, role: "root", hops: 0 },
        { node_id: c.b, role: "root", hops: 0 },
      ];
      const edges = [];
      const add = (id) => { if (!nodes.some((n) => n.node_id === id)) nodes.push({ node_id: id, hops: 1 }); };
      if (step >= 1) c.venue.forEach((v) => { add(v); edges.push({ source: c.a, target: v, predicate: "held_at", confidence: 1, provenance: "web" }, { source: c.b, target: v, predicate: "held_at", confidence: 1, provenance: "web" }); });
      if (step >= 2) {
        c.shared_artists.forEach((a) => { add(a); edges.push({ source: a, target: c.a, predicate: "performs_at", confidence: 0.8, cls: "path" }, { source: a, target: c.b, predicate: "performs_at", confidence: 0.8, cls: "path" }); });
        c.only_a.forEach((a) => { add(a); edges.push({ source: a, target: c.a, predicate: "performs_at", confidence: 0.8 }); });
        c.only_b.forEach((a) => { add(a); edges.push({ source: a, target: c.b, predicate: "performs_at", confidence: 0.8 }); });
      }
      if (step >= 3) c.shared_brands.forEach((b) => { add(b); edges.push({ source: c.a, target: b, predicate: "promoted_by", confidence: 1, cls: "clash", label: "same promoter" }, { source: c.b, target: b, predicate: "promoted_by", confidence: 1, cls: "clash", label: "same promoter" }); });
      if (step >= 4) edges.push({ source: c.a, target: c.b, predicate: "same_as", cls: c.truth === "duplicate" ? "path" : "clash", label: c.truth === "duplicate" ? "merged by an admin" : "never merged", confidence: 1 });
      this.view.set(nodes, edges, { layout: "cose" });
      const icon = { pos: "+", neg: "−", neu: "·" };
      $("#ddCaseCard", this.el).innerHTML = `
        ${EKG.eventCard(c.a)}<div style="height:8px"></div>${EKG.eventCard(c.b)}
        <div class="signal-list" style="margin-top:14px">${steps.slice(0, step + 1).map((s) => `<div class="signal"><span class="ic ${s.sig}">${icon[s.sig]}</span><div><b>${s.title}</b><div>${s.text}</div></div></div>`).join("")}</div>
        <div class="btn-row" style="justify-content:space-between;margin-top:12px">
          <button class="btn ghost" id="ddStepBack" ${step === 0 ? "disabled" : ""}>← Back</button>
          <button class="btn primary" id="ddStepNext">${step >= steps.length - 1 ? "Next case →" : "Next fact →"}</button>
        </div>`;
      $("#ddStepBack", this.el).onclick = () => { this.caseStep--; this.drawCase(); };
      $("#ddStepNext", this.el).onclick = () => {
        if (this.caseStep >= steps.length - 1) {
          const rows = $$("#ddCases .row", this.el);
          rows[(this.caseIndex + 1) % rows.length].click();
        } else {
          this.caseStep++;
          this.drawCase();
        }
      };
      $("#ddTitle", this.el).innerHTML = `<b>${steps[step].title.replace(/^\d · /, "")}</b> · each step adds one kind of fact to the picture`;
      $("#ddLegend", this.el).innerHTML = EKG.legendHTML(["event", "artist", "venue", "organizer_brand"], [["path", "points to one show"]]);
    },

    /* ---------------- why titles fail ---------------- */
    renderWhy() {
      const body = $("#ddBody", this.el);
      const max = 0.45;
      body.innerHTML = `
        <h2>Title matching can't see a third of the work.</h2>
        <div class="stats">
          <div class="stat big accent"><div class="v">38.6%</div><div class="k">of 142,781 admin merges had <b>different titles</b>. Exact matching never sees them.</div></div>
          <div class="stat big"><div class="v">97.7%</div><div class="k">of merges happen <b>before the event</b>, a median 12 days after the listing was created. That's time to catch them.</div></div>
        </div>
        <div class="card"><h3>How often a candidate pair really is a duplicate</h3>
          <p class="muted" style="margin-bottom:10px">Backtest on 587,254 events from 2024. The share of pairs that ops later merged. This is a lower bound, because ops hasn't looked at every pair.</p>
          <div class="bars">${RULES.map(([k, v, kind]) => `<div class="bar-row" data-tip="${esc(k)}: ${(v * 100).toFixed(1)}% merged"><span class="lbl">${esc(k)}</span><span class="bar-track"><span class="bar-fill ${kind === "graph" ? "accent" : kind === "base" ? "muted" : ""}" style="width:0" data-w="${v / max}"></span></span><span class="val">${(v * 100).toFixed(1)}%</span></div>`).join("")}</div>
        </div>
        <div class="card"><h3>What each graph signal is worth</h3>
          <p class="muted" style="margin-bottom:10px">60,000 NL same-venue, same-day pairs, read from the graph. 1× means the signal tells you nothing. Base rate 11.1%.</p>
          <div class="bars">${LIFTS.map(([k, v, note, kind]) => `<div class="bar-row" data-tip="${esc(k)}: ${v}× the base rate${note ? " · " + esc(note) : ""}"><span class="lbl">${esc(k)}</span><span class="bar-track"><span class="baseline-mark" style="left:${(1 / 3.6) * 100}%"></span><span class="bar-fill ${kind === "neg" ? "neg" : kind === "neu" ? "muted" : ""}" style="width:0" data-w="${v / 3.6}"></span></span><span class="val">${v}×</span></div>`).join("")}</div>
          <p class="muted" style="margin-top:10px">Two surprises. The strongest signal is <b>one hop</b> away, not a deep pattern. And the graph's best trick is often <b>ruling pairs out</b>: a shared promoter or similar-sounding acts make a duplicate less likely.</p>
        </div>
        <div class="callout"><b>The catch:</b> a shared artist only fires on ~3% of pairs, because <b>71%</b> of candidate pairs have no artist on either side. Linking artists and finding duplicates need each other. That's why we built them as one project.</div>`;
      requestAnimationFrame(() => $$(".bar-fill", body).forEach((b) => (b.style.width = `${Math.min(1, b.dataset.w) * 100}%`)));
      $$(".bar-row", body).forEach((row) => {
        row.onmousemove = (e) => EKG.showTip(`<b>${row.dataset.tip}</b>`, e.clientX, e.clientY);
        row.onmouseleave = EKG.hideTip;
      });
      this.drawCapaldi();
    },

    drawCapaldi() {
      const nodes = [
        { node_id: "event:capaldi-a", role: "root", label: "Lewis Capaldi - Newcastle - Exhibition Park - Jul 9, 2026" },
        { node_id: "event:capaldi-b", role: "root", label: "In the Park Newcastle presents Lewis Capaldi - 9th July" },
        { node_id: "venue:exhibition", label: "Newcastle Exhibition Park" },
        { node_id: "venue:wylam", label: "Wylam Brewery" },
        { node_id: "artist:capaldi", label: "Lewis Capaldi", size: 40 },
        { node_id: "city:newcastle", label: "Newcastle" },
      ];
      const edges = [
        { source: "event:capaldi-a", target: "venue:exhibition", predicate: "held_at", confidence: 1, provenance: "web" },
        { source: "event:capaldi-b", target: "venue:wylam", predicate: "held_at", confidence: 1, provenance: "event-engine" },
        { source: "venue:exhibition", target: "city:newcastle", predicate: "in_city", confidence: 1 },
        { source: "venue:wylam", target: "city:newcastle", predicate: "in_city", confidence: 1 },
        { source: "artist:capaldi", target: "event:capaldi-a", predicate: "performs_at", confidence: 0.8, provenance: "web" },
        { source: "artist:capaldi", target: "event:capaldi-b", predicate: "performs_at", confidence: 0.8, provenance: "event-engine" },
        { source: "event:capaldi-a", target: "event:capaldi-b", predicate: "conflicts_with", confidence: 0.6, cls: "clash", label: "same artist, same night" },
      ];
      this.view.set(nodes.map((n) => ({ ...n, tip: `<b>${esc(n.label)}</b>` })), edges, { layout: "cose" });
      $("#ddTitle", this.el).innerHTML = `The case from the April Data Audit: <b>title, venue and date string all differ</b>. The graph still connects them through the artist.`;
      $("#ddLegend", this.el).innerHTML = EKG.legendHTML(["event", "artist", "venue", "city"], [["", "fact"]]);
    },

    /* ---------------- clashes ---------------- */
    clashes() {
      const seen = new Set();
      const rank = { "same venue": 0, "same city": 1, "other city": 2 };
      return EKG.data().clashes
        .filter((c) => {
          const k = [c.event, c.other].sort().join("|");
          if (seen.has(k)) return false;
          seen.add(k);
          return true;
        })
        .sort((a, b) => rank[a.kind] - rank[b.kind]);
    },

    renderClash() {
      const list = this.clashes();
      const body = $("#ddBody", this.el);
      body.innerHTML = `
        <h2>Adding an artist can uncover a duplicate.</h2>
        <p class="lede">When the graph links an artist to an event, sometimes that artist <b>is already booked the same night</b>. That's usually not a mistake. It means the night is listed twice.</p>
        <div class="stats">
          <div class="stat"><div class="v">7.1%</div><div class="k">of 1,413 inferred links clash with an existing booking</div></div>
          <div class="stat"><div class="v">46 + 33</div><div class="k">at the same venue, or the same city (duplicated venues)</div></div>
          <div class="stat"><div class="v">4 of 21</div><div class="k">cross-city clashes were wrong links (franchise names)</div></div>
        </div>
        <div class="callout">A clash doesn't veto a link. It <b>routes</b> it: same venue or city → suggest a merge; another city → send to review.</div>
        <div class="list" id="ddClashList">${list.map((c, i) => `<button class="row" data-i="${i}"><span class="typedot" style="background:${c.kind === "same venue" ? "var(--good)" : c.kind === "same city" ? "var(--warn)" : "var(--bad)"}"></span><div class="main"><div class="title">${esc(label(c.event))}</div><div class="sub">${esc(label(c.other))}</div></div><span class="end">${c.kind}</span></button>`).join("") || `<p class="muted">No clashes in this market's run. Switch to ES for the best examples.</p>`}</div>`;
      const rows = $$("#ddClashList .row", body);
      rows.forEach((row) => (row.onclick = () => {
        rows.forEach((r) => r.classList.toggle("on", r === row));
        this.drawClash(list[+row.dataset.i]);
      }));
      const firstDefected = list.findIndex((c) => /defected closing/i.test(label(c.event)));
      const start = firstDefected >= 0 ? firstDefected : 0;
      if (rows[start]) rows[start].click();
    },

    drawClash(c) {
      const fa = facts(c.event) || {};
      const fb = facts(c.other) || {};
      const venueA = `venue:name:${fa.venue}`;
      const venueB = `venue:name:${fb.venue}`;
      const nodes = [
        { node_id: c.artist, role: "proposed", hops: 0 },
        { node_id: c.event, role: "root", hops: 1 },
        { node_id: c.other, role: "root", hops: 1 },
        { node_id: venueA, label: fa.venue, tip: `<b>${esc(fa.venue)}</b><div>${esc(fa.city)}</div>` },
      ];
      if (venueB !== venueA) nodes.push({ node_id: venueB, label: fb.venue, tip: `<b>${esc(fb.venue)}</b><div>${esc(fb.city)}</div>` });
      const edges = [
        { source: c.artist, target: c.event, predicate: "performs_at", inferred: true, confidence: 0.9, provenance: "graph_inferred", tip: "<b>New link proposed by the graph</b><div>from the ticket provider's lineup name</div>" },
        { source: c.artist, target: c.other, predicate: "performs_at", confidence: c.other_edge.confidence, provenance: c.other_edge.provenance },
        { source: c.event, target: venueA, predicate: "held_at", confidence: 1 },
        { source: c.other, target: venueB, predicate: "held_at", confidence: 1 },
        { source: c.event, target: c.other, predicate: "conflicts_with", cls: "clash", label: `same night · ${c.kind}` },
      ];
      if (venueB !== venueA) edges.push({ source: venueA, target: venueB, predicate: "same_as", cls: "clash", label: fa.city === fb.city ? "same city: one venue twice?" : "different cities" });
      this.view.set(nodes, edges, { layout: "cose" });
      const reading = c.kind === "same venue"
        ? "Same venue, same night, same artist: most likely <b>one night listed twice</b>. Suggest a merge."
        : c.kind === "same city"
          ? `Same night, same city, two venue records (${esc(fa.venue)} / ${esc(fb.venue)}): most likely <b>a duplicated venue</b>.`
          : "Different cities on one night: either a <b>wrong link</b> (e.g. a franchise name read as an artist) or a DJ really playing twice. Send to review.";
      $("#ddTitle", this.el).innerHTML = `${reading}<br><span class="muted">${esc(EKG.dateLabel(fa.date))}</span>`;
      $("#ddLegend", this.el).innerHTML = EKG.legendHTML(["event", "artist", "venue"], [["dash", "inferred link"]]);
    },

    /* ---------------- artist duplicates ---------------- */
    renderArtists() {
      const groups = EKG.data().artist_dedup;
      const body = $("#ddBody", this.el);
      body.innerHTML = `
        <h2>${groups[0] ? `${groups[0].members.length} artists called ${esc(groups[0].label)}, or one?` : "Same name, same act?"}</h2>
        <p class="lede">Artist rows get duplicated too, and duplicates ruin everything downstream: a new event gets linked to the wrong copy. Names can't settle it. <b>Where an act has played, and with whom</b>, can.</p>
        <div class="stats">
          <div class="stat big accent"><div class="v">2,968</div><div class="k">surplus artist rows found, in 2,533 clusters</div></div>
          <div class="stat"><div class="v">100 / 100</div><div class="k">reviewed merges were the same act (≥97% precise)</div></div>
          <div class="stat"><div class="v">89.6%</div><div class="k">of admin-verified duplicates found (recall)</div></div>
        </div>
        <p class="muted">Method: probabilistic record linkage. It scores each candidate pair on name, shared venues, shared cities, shared co-performers and similar artists. Reviewed by hand on 139 pairs, 20 per score band.</p>
        <div class="list" id="ddGroups">${groups.map((g, i) => `<button class="row" data-i="${i}"><span class="typedot" style="background:var(--t-artist)"></span><div class="main"><div class="title">${esc(g.label)} ×${g.members.length}</div><div class="sub">${g.members.map((m) => m.events).join(" / ")} events</div></div></button>`).join("")}</div>
        <div id="ddPairs"></div>`;
      const rows = $$("#ddGroups .row", body);
      rows.forEach((row) => (row.onclick = () => {
        rows.forEach((r) => r.classList.toggle("on", r === row));
        this.drawGroup(groups[+row.dataset.i]);
      }));
      const start = groups.findIndex((g) => /stingray/i.test(g.label));
      if (rows[start >= 0 ? start : 0]) rows[start >= 0 ? start : 0].click();
    },

    drawGroup(g) {
      const nodes = [];
      const edges = [];
      const add = (id, extra = {}) => { if (!nodes.some((n) => n.node_id === id)) nodes.push({ node_id: id, ...extra }); };
      const sharedVenues = new Set(g.pairs.flatMap((p) => p.shared.venues));
      const sharedCo = new Set(g.pairs.flatMap((p) => p.shared.cobilled));
      g.members.forEach((m, i) => {
        add(m.id, { role: "root", label: `${EKG.short(g.label, 18)} · row ${i + 1} (${m.events} events)`, size: 30 + Math.min(20, m.events / 3) });
        m.venues.forEach((v) => { add(v); edges.push({ source: m.id, target: v, predicate: "held_at", label: "played at", confidence: sharedVenues.has(v) ? 1 : 0.4, cls: sharedVenues.has(v) ? "path" : "" }); });
      });
      g.pairs.forEach((p) => {
        p.shared.cobilled.slice(0, 6).forEach((a) => {
          add(a);
          edges.push({ source: p.a, target: a, predicate: "performs_at", label: "shared a bill with", confidence: 1, cls: "path" }, { source: p.b, target: a, predicate: "performs_at", label: "shared a bill with", confidence: 1, cls: "path" });
        });
      });
      this.view.set(nodes, edges, { layout: "cose" });
      const best = g.pairs.reduce((a, p) => (p.counts.venues + p.counts.cobilled > a.counts.venues + a.counts.cobilled ? p : a), g.pairs[0]);
      const strong = best.counts.venues + best.counts.cobilled + best.counts.similar >= 5;
      $("#ddPairs", this.el).innerHTML = `<div class="card"><h3>${strong ? "Looks like one act, listed more than once" : "Probably different acts with the same name"}</h3><p class="muted">Most-connected pair: ${best.counts.venues} shared venues, ${best.counts.cities} shared cities, ${best.counts.cobilled} shared co-performers, ${best.counts.similar} shared similar artists.${strong ? "" : " Their histories barely touch: different scenes, different cities."}</p></div>`;
      $("#ddTitle", this.el).innerHTML = `<b>${esc(g.label)}</b>: each orange node is a separate artist row. Blue lines are what the rows <b>share</b>.`;
      $("#ddLegend", this.el).innerHTML = EKG.legendHTML(["artist", "venue"], [["path", "shared history"]]);
    },

    render() { this.caseIndex = 0; this.caseStep = 0; this.show(this.sub); },

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
