/* shared state, data access and the plain-english vocabulary every chapter speaks */
window.EKG = (function () {
  const TYPE = {
    event: { name: "Event", plural: "events", color: "#3987e5", shape: "round-rectangle" },
    artist: { name: "Artist", plural: "artists", color: "#d95926", shape: "ellipse" },
    venue: { name: "Venue", plural: "venues", color: "#199e70", shape: "round-diamond" },
    organizer_brand: { name: "Promoter", plural: "promoters", color: "#9085e9", shape: "round-hexagon" },
    external_event: { name: "Ticket-provider listing", plural: "provider listings", color: "#c98500", shape: "round-tag" },
    series: { name: "Series", plural: "series", color: "#d55181", shape: "barrel" },
    city: { name: "City", plural: "cities", color: "#8b8e98", shape: "round-triangle" },
    genre: { name: "Genre", plural: "genres", color: "#8b8e98", shape: "star" },
    ticket_provider: { name: "Ticket provider", plural: "ticket providers", color: "#8b8e98", shape: "vee" },
    organizer_company: { name: "Organizer company", plural: "organizer companies", color: "#8b8e98", shape: "rhomboid" },
  };

  // how each predicate reads as a sentence, source -> target
  const VERB = {
    performs_at: "performs at",
    held_at: "takes place at",
    in_city: "is in",
    promoted_by: "is promoted by",
    owned_by: "is owned by",
    sold_via: "sells tickets via",
    imported_as: "was imported as",
    has_genre: "has genre",
    series_of: "is part of",
    similar_to: "sounds like",
    same_as: "is a duplicate listing of",
    conflicts_with: "clashes with",
    "same name": "shares a name with the lineup entry for",
  };

  const PREDICATE_NAME = {
    performs_at: "performs at", held_at: "held at", in_city: "in city", promoted_by: "promoted by",
    owned_by: "owned by", sold_via: "sold via", imported_as: "imported as", has_genre: "has genre",
    series_of: "series", similar_to: "similar to", same_as: "same as (merged)", conflicts_with: "conflicts with",
  };

  const SOURCE = {
    employee: "a TicketSwap employee",
    "event-engine": "an Event Engine import",
    web: "the seller who created the event",
    "auto-matching-rule": "an automatic title-matching rule",
    automation: "an automation",
    "redirected-artist": "an artist merge",
    chartmetric: "Chartmetric (music data)",
    event_redirect: "an admin merging two listings",
    venue_redirect: "an admin merging two venues",
    artist_redirect: "an admin merging two artists",
    partnerships: "the partnerships team",
    secure_swap: "a SecureSwap integration",
    graph_inferred: "the graph (inferred)",
    event_tag_manual: "an employee tagging it",
    event_tag_automatic: "automatic tagging",
    candidate: "a same-name candidate",
  };

  const SOURCE_CLASS = {
    admin_verified: "verified by an admin",
    seller_input: "typed by a seller",
    event_engine_import: "imported from a ticket provider",
    system_rule: "set by an automatic rule",
    third_party: "from a third party",
    system_of_record: "from the core database",
    graph_inferred: "inferred by the graph",
  };

  const SIGNAL = {
    provider: { name: "Ticket provider lists the name", why: "the ticket provider's lineup names this act" },
    venue_history: { name: "Played this venue before", why: "they have played this venue before" },
    city_history: { name: "Played this city before", why: "they have played this city before" },
    brand_history: { name: "Worked with this promoter", why: "this promoter has booked them before" },
    series_history: { name: "Part of this series before", why: "they played an earlier edition of this series" },
    cobilled_before: { name: "Shared a bill with this lineup", why: "they have shared a stage with others on this bill" },
    similar_to_cobilled: { name: "Sounds like this lineup", why: "they sound like acts on this bill" },
  };

  const state = {
    market: "NL",
    data: {},        // market -> bundle
    junk: null,
    chapters: [],
    current: null,
    me: null,
    cid: null,
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const h = (html) => {
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => (n == null ? "–" : Number(n).toLocaleString("en-US"));
  const pct = (x, digits = 1) => (x == null ? "–" : (100 * x).toFixed(digits) + "%");

  async function loadMarket(market) {
    if (state.data[market]) return state.data[market];
    const res = await fetch(`data/${market.toLowerCase()}.json`);
    if (!res.ok) throw new Error(`could not load data/${market.toLowerCase()}.json (${res.status})`);
    const bundle = await res.json();
    bundle.order = Object.keys(bundle.nodes);
    bundle.adj = buildAdjacency(bundle);
    state.data[market] = bundle;
    return bundle;
  }

  /* the searchable index: every node a visitor can type, with the shard holding its payloads */
  async function loadIndex(market = state.market) {
    const bundle = await loadMarket(market);
    if (bundle.index) return bundle.index;
    const res = await fetch(`data/${market.toLowerCase()}-index.json`);
    const rows = res.ok ? await res.json() : [];
    bundle.index = rows.map(([id, l, t, d, rec, shard]) => ({ id, l, t, d, rec, shard }));
    bundle.indexById = new Map(bundle.index.map((r) => [r.id, r]));
    return bundle.index;
  }

  /* a shard's labels and facts join the market's tables, so label() works on anything it names */
  async function loadShard(id, market = state.market) {
    const bundle = await loadMarket(market);
    await loadIndex(market);
    const row = bundle.indexById.get(id);
    if (!row) return null;
    bundle.shards = bundle.shards || {};
    if (!bundle.shards[row.shard]) {
      bundle.shards[row.shard] = fetch(`data/${market.toLowerCase()}/${row.shard}.json`).then((r) => r.json()).then(decodeShard).then((shard) => {
        for (const [nid, [t, l, d]] of Object.entries(shard.nodes)) if (!bundle.nodes[nid]) bundle.nodes[nid] = { t, l, d };
        Object.assign(bundle.facts, shard.facts);
        return shard;
      });
    }
    return bundle.shards[row.shard];
  }

  const IMAGE_PREFIX = "https://ticketswap-image-cdn.b-cdn.net/public/";

  /* shards use small per-file integers for node ids; turn them back into real ids */
  function decodeShard(raw) {
    const ids = raw.n.map((r) => r[0]);
    const nodes = Object.fromEntries(raw.n.map(([id, t, l, d]) => [id, [t, l, d]]));
    const explore = {};
    for (const [k, x] of Object.entries(raw.x)) {
      explore[ids[k]] = {
        n: x.n.map(([i, h]) => [ids[i], h]),
        e: x.e.map(([a, b, p, src, c, inf]) => [ids[a], ids[b], p, src, c, inf]),
        r: x.r,
      };
    }
    const rec = {};
    for (const [k, r] of Object.entries(raw.r)) {
      rec[ids[k]] = {
        artists: r.a.map(([i, m]) => [ids[i], m]),
        events: r.e.map(([i, m, own]) => [ids[i], m, own]),
        paths: Object.fromEntries(Object.entries(r.p).map(([t, path]) => [ids[t], path.map((i) => ids[i])])),
      };
    }
    const facts = {};
    for (const [k, [date, city, venue, img, upcoming]] of Object.entries(raw.f)) {
      facts[ids[k]] = { date, city, venue, img: img ? (img.startsWith("http") ? img : IMAGE_PREFIX + img) : null, upcoming: !!upcoming };
    }
    return { nodes, explore, rec, facts };
  }

  async function loadJunk() {
    if (!state.junk) state.junk = await (await fetch("data/junk.json")).json();
    return state.junk;
  }

  function buildAdjacency(bundle) {
    const adj = new Map();
    const push = (a, item) => {
      if (!adj.has(a)) adj.set(a, []);
      adj.get(a).push(item);
    };
    bundle.universe.forEach(([s, t, predicate, source, confidence], i) => {
      const src = bundle.order[s];
      const dst = bundle.order[t];
      const edge = { edge_id: `u${i}`, source: src, target: dst, predicate, provenance: source, confidence };
      push(src, edge);
      push(dst, edge);
    });
    return adj;
  }

  const data = () => state.data[state.market];
  const node = (id, bundle = data()) => (bundle && bundle.nodes[id]) || { t: String(id).split(":")[0], l: id, d: 0 };
  const label = (id, bundle) => node(id, bundle).l;
  const type = (id, bundle) => node(id, bundle).t;
  const facts = (id, bundle = data()) => (bundle && bundle.facts[id]) || null;

  function dateLabel(iso) {
    if (!iso) return "";
    const d = new Date(iso + "T12:00:00");
    return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  }

  function eventMeta(id, bundle) {
    const f = facts(id, bundle);
    if (!f) return "";
    return [dateLabel(f.date), f.venue, f.city].filter(Boolean).join(" · ");
  }

  function eventCard(id, { tall = false, extra = "" } = {}) {
    const f = facts(id) || {};
    const img = f.img ? `style="background-image:url('${esc(f.img)}')"` : "";
    return `<div class="event-card ${tall ? "tall" : ""}">
      <div class="img" ${img}></div>
      <div class="body">
        <div class="title">${esc(label(id))}</div>
        <div class="meta">${esc(eventMeta(id))}</div>
        ${extra}
      </div></div>`;
  }

  function typeDot(t) {
    return `<span class="typedot" style="background:${(TYPE[t] || TYPE.city).color}"></span>`;
  }

  function sentence(edge, bundle) {
    const verb = VERB[edge.predicate] || edge.predicate;
    return `${label(edge.source, bundle)} ${verb} ${label(edge.target, bundle)}`;
  }

  function provenance(edge) {
    if (edge.inferred || edge.provenance === "graph_inferred") return "proposed by the graph";
    const who = SOURCE[edge.provenance] || edge.provenance || "unknown source";
    return `according to ${who}`;
  }

  // tooltip
  const tip = () => $("#tooltip");
  function showTip(html, x, y) {
    const el = tip();
    el.innerHTML = html;
    el.classList.add("on");
    const r = el.getBoundingClientRect();
    const left = Math.min(window.innerWidth - r.width - 10, x + 14);
    const top = y + r.height + 20 > window.innerHeight ? y - r.height - 12 : y + 14;
    el.style.left = `${Math.max(8, left)}px`;
    el.style.top = `${Math.max(8, top)}px`;
  }
  function hideTip() { tip().classList.remove("on"); }

  function nodeTip(id) {
    const n = node(id);
    const t = TYPE[n.t] || TYPE.city;
    const meta = n.t === "event" ? eventMeta(id) : "";
    return `<div class="tt-type" style="color:${t.color}">${t.name}</div><b>${esc(n.l)}</b>${meta ? `<div>${esc(meta)}</div>` : ""}${n.d ? `<div class="muted">${fmt(n.d)} connections in the graph</div>` : ""}`;
  }

  function edgeTip(edge) {
    const conf = edge.confidence != null ? ` · confidence ${Math.round(edge.confidence * 100)}%` : "";
    return `<b>${esc(sentence(edge))}</b><div>${esc(provenance(edge))}${conf}</div>`;
  }

  // chapters
  function register(chapter) { state.chapters.push(chapter); }

  function chapterIndex(id) { return state.chapters.findIndex((c) => c.id === id); }

  async function go(id, { push = true } = {}) {
    const chapter = state.chapters.find((c) => c.id === id) || state.chapters[0];
    if (state.current === chapter) return;
    const previous = state.current;
    state.current = chapter;
    $$("#chapters button").forEach((b) => b.classList.toggle("on", b.dataset.go === chapter.id));
    $$(".chapter").forEach((el) => el.classList.toggle("on", el.id === `ch-${chapter.id}`));
    if (previous && previous.leave) previous.leave();
    if (push) history.replaceState(null, "", `#${chapter.id}`);
    await chapter.enter();
  }

  function step(delta) {
    const i = chapterIndex(state.current.id);
    const next = state.chapters[Math.max(0, Math.min(state.chapters.length - 1, i + delta))];
    go(next.id);
  }

  async function setMarket(market) {
    if (market === state.market) return;
    state.market = market;
    $$(".market button").forEach((b) => b.classList.toggle("on", b.dataset.market === market));
    await loadMarket(market);
    state.chapters.forEach((c) => c.marketChanged && (c.dirty = true));
    if (state.current) {
      state.current.dirty = true;
      await state.current.enter();
    }
  }

  function once(fn) {
    let done = false;
    return (...args) => {
      if (done) return;
      done = true;
      fn(...args);
    };
  }

  return {
    TYPE, VERB, PREDICATE_NAME, SOURCE, SOURCE_CLASS, SIGNAL, state,
    $, $$, h, esc, fmt, pct, loadMarket, loadIndex, loadShard, loadJunk, data, node, label, type, facts,
    dateLabel, eventMeta, eventCard, typeDot, sentence, provenance,
    showTip, hideTip, nodeTip, edgeTip, register, go, step, setMarket, once,
  };
})();
