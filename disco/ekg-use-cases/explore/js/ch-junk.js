/* chapter 7: junk hubs -- nodes that stand in for many unrelated things, found by rules and flagged */
(function () {
  const { $, $$, esc, fmt } = EKG;

  const KIND = {
    placeholder: { name: "Placeholders", what: "a stand-in for “we don't know”", verdict: "junk" },
    theme: { name: "Themes", what: "a kind of night, not an act", verdict: "junk" },
    city_as_venue: { name: "Venues that are a city", what: "a venue record that is really the whole city", verdict: "junk" },
    deletion_sink: { name: "Deletion sinks", what: "a deleted listing that duplicates were merged into", verdict: "junk" },
    overloaded: { name: "Real acts, too many places", what: "a real act booked in too many cities at once, so some links are wrong", verdict: "review" },
    automation_magnet: { name: "Common-word acts", what: "a real act whose name is a common word, so title automation attaches it to anything containing it", verdict: "review" },
  };

  const chapter = {
    id: "junk",
    title: "Junk hubs",
    view: null,
    hub: null,
    flagged: false,
    dirty: true,

    init(stage) {
      this.el = EKG.h(`<section class="chapter" id="ch-junk"><div class="split">
        <div class="story">
          <div class="eyebrow">Hygiene · the trap that breaks everything else</div>
          <h2>Some nodes lie.</h2>
          <p class="lede">A <b>junk hub</b> is one node standing in for many unrelated things: an artist called <b>“Unbekannt”</b> (German for <i>unknown</i>), a venue called <b>“Amsterdam”</b>. To a graph they look like the best-connected things in Europe, and they glue unrelated events together.</p>
          <div class="card" id="jkFound"></div>
          <div id="jkHubs"></div>
        </div>
        <div class="canvas-wrap">
          <div class="cy" id="jkCy"></div>
          <div class="canvas-title" id="jkTitle"></div>
          <div class="canvas-caption"><div id="jkLegend"></div><div class="btn-row" style="pointer-events:auto">
            <button class="btn" id="jkDay">Show one night</button>
            <button class="btn primary" id="jkFlag">Flag it</button>
            <button class="btn ghost" id="jkReset">Reset</button>
          </div></div>
          <div id="jkText" style="position:absolute;right:18px;top:78px;width:min(380px,40%);pointer-events:none"></div>
        </div>
      </div></section>`);
      stage.appendChild(this.el);
      this.view = new EKG.GraphView($("#jkCy", this.el), { minZoom: 0.05 });
      $("#jkDay", this.el).onclick = () => this.showDay();
      $("#jkFlag", this.el).onclick = () => this.flag();
      $("#jkReset", this.el).onclick = () => this.draw(this.hub);
    },

    renderFound() {
      const j = this.junk;
      const byKind = {};
      j.counts.forEach(([verdict, type, kind, n]) => {
        const k = byKind[kind] || (byKind[kind] = { junk: 0, review: 0, type });
        k[verdict] += n;
      });
      const rows = Object.entries(byKind).sort((a, b) => (b[1].junk + b[1].review) - (a[1].junk + a[1].review));
      $("#jkFound", this.el).innerHTML = `
        <h3>Found by rules, out of the gate</h3>
        <div class="stats" style="margin:10px 0">
          <div class="stat accent"><div class="v">${fmt(j.junk)}</div><div class="k">flagged as junk: stop walking through them</div></div>
          <div class="stat"><div class="v">${fmt(j.review)}</div><div class="k">real nodes with suspicious links: for review</div></div>
        </div>
        <div class="list">${rows.map(([kind, c]) => `<div class="row" style="cursor:default"><div class="main"><div class="title">${esc(KIND[kind]?.name || kind)}</div><div class="sub">${esc(KIND[kind]?.what || "")}</div></div><span class="end">${c.junk ? `<b style="color:var(--bad)">${fmt(c.junk)}</b> junk` : ""}${c.junk && c.review ? " · " : ""}${c.review ? `${fmt(c.review)} review` : ""}</span></div>`).join("")}</div>
        <p class="muted small" style="margin-top:10px">No hand-made list: rules find them. A placeholder is <b>never named in its own events' titles</b> (Unbekannt 0%, real acts near 100%). A real artist can't play <b>3+ cities on one night</b>, again and again. A venue named after <b>its own city</b> with 20+ events is the city, not a venue.</p>`;
    },

    renderHubs() {
      const hubs = this.junk.hubs.filter((h) => h.events.length >= 8);
      const kinds = [...new Set(hubs.map((h) => h.kind))];
      $("#jkHubs", this.el).innerHTML = kinds.map((k) => `
        <div class="eyebrow" style="margin:6px 0">${esc(KIND[k]?.name || k)}${KIND[k]?.verdict === "review" ? " · review" : ""}</div>
        <div class="list">${hubs.filter((h) => h.kind === k).map((h) => `<button class="row" data-id="${esc(h.id)}"><span class="typedot" style="background:${h.verdict === "junk" ? "var(--bad)" : "var(--warn)"}"></span><div class="main"><div class="title">${esc(h.label)}</div><div class="sub">${esc(EKG.TYPE[h.type]?.name || h.type)} · ${fmt(h.degree)} connections</div></div></button>`).join("")}</div>`).join("");
      $$("#jkHubs .row", this.el).forEach((b) => (b.onclick = () => this.draw(this.junk.hubs.find((h) => h.id === b.dataset.id))));
    },

    groupKey(hub) {
      // an artist hub's events really differ by place; a venue hub's all share a city, so by kind of event
      return hub.type === "artist" ? "city" : "cat";
    },

    draw(hub) {
      this.hub = hub;
      this.flagged = false;
      $$("#jkHubs .row", this.el).forEach((b) => b.classList.toggle("on", b.dataset.id === hub.id));
      const tip = (e) => `<div class="tt-type" style="color:var(--t-event)">Event</div><b>${esc(e.title)}</b><div>${esc(EKG.dateLabel(e.date))} · ${esc(e.venue || "")} · ${esc(e.city)}</div><div class="muted">${esc(e.cat)}</div>`;
      const verb = hub.type === "artist" ? "performs at" : "hosts";
      const hubId = `hub:${hub.id}`;
      const nodes = [{ node_id: hubId, role: "root", label: hub.label, size: 90, cls: hub.verdict === "junk" ? "junk" : "", color: hub.verdict === "junk" ? undefined : "#e0a526", tip: `<b>${esc(hub.label)}</b><div>${fmt(hub.degree)} connections · ${esc(KIND[hub.kind]?.what || "")}</div><div class="muted">${esc(hub.reason)}</div>` }];
      const edges = [];
      hub.events.forEach((e, i) => {
        nodes.push({ node_id: e.id, label: "", size: 20, hops: 1 + (i % 4), tip: tip(e) });
        edges.push({ source: hubId, target: e.id, predicate: "performs_at", confidence: 0.3, cls: "junk-edge", tip: `<b>${esc(hub.label)}</b> ${verb} <b>${esc(e.title)}</b>` });
      });
      this.view.set(nodes, edges, { layout: "concentric", root: hubId, labels: "none" });
      const cities = new Set(hub.events.map((e) => e.city)).size;
      const spread = hub.type === "artist" ? `spread over ${fmt(cities)} cities` : `${fmt(new Set(hub.events.map((e) => e.cat)).size)} different kinds of event`;
      $("#jkTitle", this.el).innerHTML = `<b>${esc(hub.label)}</b> · ${esc(KIND[hub.kind]?.what || "")}<br><span class="muted">${fmt(hub.events.length)} of its events, ${spread}. Every line says “${esc(hub.label)} ${verb} this event”.</span>`;
      $("#jkLegend", this.el).innerHTML = EKG.legendHTML(["event", "city"]);
      $("#jkDay", this.el).style.display = hub.type === "artist" ? "" : "none";
      $("#jkFlag", this.el).textContent = hub.verdict === "junk" ? "Flag it as junk" : "Flag its links for review";
      const extra = /unbekannt/i.test(hub.label) ? " Before flagging, this one name caused <b>70%</b> of all upcoming “same artist, same night” conflicts." : "";
      $("#jkText", this.el).innerHTML = `<div class="callout ${hub.verdict === "junk" ? "warn" : ""}"><b>Why it's ${hub.verdict === "junk" ? "junk" : "suspicious"}:</b> ${esc(hub.reason)}.${extra} Right now any two of these events are just <b>two steps apart</b>, through this one node.</div>`;
    },

    showDay() {
      const hub = this.hub;
      if (!hub.busiest_date) return;
      const ids = hub.events.filter((e) => e.date === hub.busiest_date).map((e) => e.id);
      const edges = this.view.cy.edges().filter((e) => ids.includes(e.target().id())).map((e) => e.id());
      this.view.highlight([`hub:${hub.id}`, ...ids], edges);
      $("#jkText", this.el).innerHTML = `<div class="callout warn">On <b>${esc(EKG.dateLabel(hub.busiest_date))}</b>, “${esc(hub.label)}” appears at <b>${fmt(hub.busiest_date_events)}</b> of these events in <b>${fmt(hub.busiest_date_cities)}</b> different cities. No act can do that: an <b>impossible schedule</b>, one of the rules that catches a hub.</div>`;
    },

    flag() {
      if (this.flagged) return;
      this.flagged = true;
      const hub = this.hub;
      const cy = this.view.cy;
      cy.elements().removeClass("hl dim");
      const center = cy.getElementById(`hub:${hub.id}`);
      center.animate({ style: { opacity: 0 } }, { duration: 700, complete: () => center.remove() });
      cy.edges().animate({ style: { opacity: 0 } }, { duration: 500, complete: () => cy.edges().remove() });
      const key = this.groupKey(hub);
      const groups = {};
      hub.events.forEach((e) => (groups[e[key]] = groups[e[key]] || []).push(e));
      const n = Object.keys(groups).length;
      setTimeout(() => {
        const added = [];
        Object.entries(groups).forEach(([g, evs]) => {
          const id = `group:${g}`;
          added.push({ group: "nodes", data: { id, color: "#8b8e98", shape: key === "city" ? "round-triangle" : "round-rectangle", size: 10 + 3 * Math.sqrt(evs.length), short: evs.length > 2 ? g : "", tip: `<b>${esc(g)}</b><div>${evs.length} of the sampled events</div>` } });
          evs.forEach((e) => added.push({ group: "edges", data: { id: `${e.id}-g`, source: e.id, target: id, width: 1, raw: { source: e.id, target: id, predicate: key === "city" ? "in_city" : "has_genre" }, tip: `<b>${esc(e.title)}</b><div>${key === "city" ? "is in" : "is a"} ${esc(g)}</div>` } }));
        });
        cy.add(added);
        cy.layout({ name: "cose", animate: true, animationDuration: 1200, nodeRepulsion: () => 4500, idealEdgeLength: () => 26, gravity: 0.6, componentSpacing: 18, padding: 40, randomize: false }).run();
      }, 750);
      const biggest = Object.entries(groups).sort((a, b) => b[1].length - a[1].length).slice(0, 2).map(([g]) => g);
      const what = key === "city" ? "cities" : "kinds of event";
      $("#jkTitle", this.el).innerHTML = `Flagged. The same ${fmt(hub.events.length)} events now fall into <b>${fmt(n)}</b> separate islands, one per ${key === "city" ? "city" : "kind of event"}.`;
      $("#jkText", this.el).innerHTML = `<div class="callout good"><b>Why ${fmt(n)} islands?</b> These events happen in <b>${fmt(n)}</b> different ${what}. The only thing linking an event in ${esc(biggest[0] || "one place")} to one in ${esc(biggest[1] || "another")} was “${esc(hub.label)}”. A flag tells every walk and every clustering step not to pass through it. Each event keeps its <b>real</b> connections (${key === "city" ? "its venue, and the venue's city" : "what kind of event it is"}), so the events settle into groups that have nothing to do with each other. That's what they always were.<br><br><b>Flag, don't delete:</b> the node and its lines stay in the data, so nothing is lost. ${key === "city" ? "An event whose only artist is a placeholder is really an artist-less event, and now it shows up as one to fill in." : "Deleting a city-named venue's lines would strip the venue from about 100k events."}</div>`;
    },

    async render() {
      this.junk = await EKG.loadJunk();
      this.renderFound();
      this.renderHubs();
      const first = this.junk.hubs.find((h) => /unbekannt/i.test(h.label)) || this.junk.hubs[0];
      this.draw(first);
    },

    async enter() {
      this.view.resize();
      if (this.dirty) {
        this.dirty = false;
        await this.render();
      }
    },
  };

  EKG.register(chapter);
})();
