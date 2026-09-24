/* use case 1: upcoming Spanish events that share an artist on the same night, and why each is flagged */
(function () {
  const { $, $$, esc, fmt } = EKG;

  const READINGS = {
    duplicate_listing: {
      label: 'Duplicate listing',
      sentence: 'Same artist, same venue, same night. Almost always one event listed more than once.',
    },
    duplicate_venue: {
      label: 'Same city, other venue',
      sentence: 'Same artist, same city, same night, at different venues. Usually the venue itself is duplicated ("Roig Arena" vs "Sala Multi - Roig Arena"), or one artist link is wrong.',
    },
    wrong_link: {
      label: 'Different cities',
      sentence: 'The same artist in two cities on one night. One artist link is probably wrong: a tribute show, a namesake, or a title match gone astray.',
    },
    two_shows: {
      label: 'Two shows',
      sentence: 'A title names a numbered session ("segundo pase"), so this is probably a genuine second show. It stays at low confidence in case one of them is itself a duplicate.',
    },
  };

  const FILTERS = [['all', 'All'], ...Object.entries(READINGS).map(([key, r]) => [key, r.label])];

  const titleCase = (text) => String(text || '').replace(/(^|[\s\-/])(\p{L})/gu, (_, sep, ch) => sep + ch.toUpperCase());
  const day = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  const clock = (event) => event.starts_at.slice(11);
  const place = (event) => `${event.venue_name}, ${titleCase(event.city_name)}`;

  const chapter = {
    id: 'conflicts',
    data: null,
    byId: new Map(),
    filter: 'all',
    query: '',
    current: null,
    view: null,

    async load() {
      if (this.data) return;
      const response = await fetch('data/snapshot.json');
      if (!response.ok) throw new Error(`could not load data/snapshot.json (${response.status})`);
      this.data = await response.json();
      this.byId = new Map(this.data.clusters.map((c) => [c.id, c]));
    },

    init() {
      this.view = new EKG.GraphView($('#cf-cy'));
      $('#cf-search').addEventListener('input', (e) => { this.query = e.target.value.trim().toLowerCase(); this.renderList(); });
      $('#cf-filter').addEventListener('click', (e) => {
        const b = e.target.closest('[data-f]');
        if (!b) return;
        this.filter = b.dataset.f;
        this.renderFilter();
        this.renderList();
      });
      $('#cf-list').addEventListener('click', (e) => {
        const row = e.target.closest('[data-id]');
        if (row) this.select(row.dataset.id);
      });
    },

    async enter() {
      await this.load();
      if (!this.rendered) {
        this.renderIntro();
        this.renderFilter();
        this.rendered = true;
      }
      this.renderList();
      if (!this.current) this.select(this.visible()[0]?.id);
      else this.view.resize();
    },

    visible() {
      return this.data.clusters.filter((c) => {
        if (this.filter !== 'all' && c.reading !== this.filter) return false;
        if (!this.query) return true;
        return [...c.artists, ...c.events.flatMap((e) => [e.title, e.venue_name, e.city_name])].join(' ').toLowerCase().includes(this.query);
      });
    },

    renderIntro() {
      const clusters = this.data.clusters;
      const pairs = clusters.reduce((n, c) => n + c.pairs.length, 0);
      const listings = clusters.reduce((n, c) => n + c.events.length, 0);
      const dup = clusters.filter((c) => c.reading === 'duplicate_listing' || c.reading === 'duplicate_venue').length;
      $('#cf-lede').innerHTML = `<b>${fmt(clusters.length)}</b> groups of upcoming events in Spain share an artist on the same night: <b>${fmt(pairs)}</b> clashing pairs across <b>${fmt(listings)}</b> listings. <b>${fmt(dup)}</b> of the groups look like one event, or one venue, entered twice.`;
      $('#cf-callout').innerHTML = '<b>How they are found:</b> two events share a linked artist on one date. Festivals are left out, because an artist links to a festival as a whole, and so are placeholder artists like “Unbekannt”.';
    },

    renderFilter() {
      const count = (key) => (key === 'all' ? this.data.clusters.length : this.data.clusters.filter((c) => c.reading === key).length);
      $('#cf-filter').innerHTML = FILTERS.map(([key, label]) => `<button data-f="${key}" class="${this.filter === key ? 'on' : ''}">${esc(label)} <span class="muted">${count(key)}</span></button>`).join('');
    },

    renderList() {
      const rows = this.visible();
      $('#cf-list').innerHTML = rows.length === 0
        ? '<p class="muted">Nothing matches.</p>'
        : rows.slice(0, 200).map((c) => `
          <button class="row ${this.current === c.id ? 'on' : ''}" data-id="${esc(c.id)}">
            ${EKG.typeDot('event')}
            <div class="main">
              <div class="title">${esc(c.artists.join(', '))}</div>
              <div class="sub">${esc(day(c.event_date))} · ${c.events.length} listings · ${esc(titleCase(c.events[0]?.city_name))}</div>
            </div>
            <span class="conf-pill ${c.confidence < 0.6 ? 'low' : ''}">${c.confidence.toFixed(1)}</span>
          </button>`).join('');
    },

    select(id) {
      const cluster = this.byId.get(id);
      if (!cluster) return;
      this.current = id;
      $$('#cf-list .row').forEach((r) => r.classList.toggle('on', r.dataset.id === id));
      $(`#cf-list .row[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest' });
      this.renderGraph(cluster);
      this.renderArgument(cluster);
    },

    move(step) {
      const rows = this.visible();
      if (!rows.length) return;
      const i = rows.findIndex((c) => c.id === this.current);
      this.select(rows[Math.max(0, Math.min(rows.length - 1, i + step))].id);
    },

    renderGraph(cluster) {
      const listings = new Map(cluster.events.map((e) => [e.node_id, e]));
      const shared = new Set(cluster.pairs.flatMap((p) => p.shared_artists));
      const typeName = (t) => (EKG.TYPE[t] || EKG.TYPE.city).name;
      const nodes = cluster.nodes.map((n) => {
        const event = listings.get(n.id);
        const tip = event
          ? `<div class="tt-type" style="color:var(--t-event)">Listing</div><b>${esc(event.title)}</b><div>${esc(clock(event))} · ${esc(place(event))}</div>`
          : `<div class="tt-type" style="color:var(--t-${esc(n.type)})">${esc(typeName(n.type))}</div><b>${esc(n.label)}</b>${n.degree ? `<div class="muted">${fmt(n.degree)} connections in the graph</div>` : ''}`;
        return {
          node_id: n.id,
          label: n.label.length > 26 ? `${n.label.slice(0, 25)}…` : n.label,
          role: event ? 'target' : '',
          size: shared.has(n.id) ? 38 : undefined,
          tip,
        };
      });
      const label = (id) => cluster.nodes.find((n) => n.id === id)?.label || id;
      const edges = cluster.edges.map((e) => ({
        ...e,
        tip: `<b>${esc(label(e.source))}</b> ${esc(EKG.VERB[e.predicate] || e.predicate)} <b>${esc(label(e.target))}</b><div>${esc(EKG.provenance(e))}</div>`,
      }));
      const labelled = cluster.pairs.length <= 4;
      cluster.pairs.forEach((p) => {
        edges.push({
          source: p.src,
          target: p.dst,
          predicate: 'conflicts_with',
          confidence: p.confidence,
          cls: 'clash',
          // an empty label would fall back to the predicate name, so a busy group gets a zero-width one
          label: labelled ? (p.is_same_venue ? 'same venue' : 'same night') : '\u200b',
          tip: `<b>${esc(label(p.src))}</b> clashes with <b>${esc(label(p.dst))}</b><div>${p.is_same_venue ? 'same venue, same night' : 'same night, different venues'} · confidence ${p.confidence.toFixed(1)}</div>`,
        });
      });
      this.view.set(nodes, edges, { layout: 'cose' });
      const types = [...new Set(cluster.nodes.map((n) => n.type))].filter((t) => EKG.TYPE[t]);
      $('#cf-legend').innerHTML = EKG.legendHTML(types, [['clash', 'clash: same artist, same night']]);
      $('#cf-title').innerHTML = `<b>${esc(cluster.artists.join(', '))}</b> · ${esc(day(cluster.event_date))}<br><span class="muted">${cluster.events.length} listings with a white ring; red dashed lines are the clashes. Hover anything for details.</span>`;
    },

    renderArgument(cluster) {
      const reading = READINGS[cluster.reading];
      const byNode = new Map(cluster.events.map((e) => [e.node_id, e]));
      const listingRows = cluster.events.map((e) => `
        <div class="row static" data-hl-nodes="${esc(e.node_id)}">
          <div class="main">
            <div class="title">${esc(e.title)}</div>
            <div class="sub">${esc(clock(e))} · ${esc(place(e))}</div>
          </div>
          <span class="end links">${e.frontend_url ? `<a href="${esc(e.frontend_url)}" target="_blank" rel="noopener">page</a>` : ''}${e.admin_url ? `<a href="${esc(e.admin_url)}" target="_blank" rel="noopener">admin</a>` : ''}</span>
        </div>`).join('');
      const pairRows = cluster.pairs.slice(0, 6).map((p) => {
        const a = byNode.get(p.src);
        const b = byNode.get(p.dst);
        const facts = [p.is_same_venue ? 'same venue' : 'different venues', p.is_possible_second_session ? 'a title names a second show' : null].filter(Boolean).join(' · ');
        return `
          <div class="row static" data-hl-nodes="${esc(p.src)} ${esc(p.dst)}" data-hl-pair="${esc(p.src)}|${esc(p.dst)}">
            <div class="main">
              <div class="title">${esc(clock(a))} ${esc(a.venue_name)} ↔ ${esc(clock(b))} ${esc(b.venue_name)}</div>
              <div class="sub">${esc(facts)}</div>
            </div>
            <span class="conf-pill ${p.confidence < 0.6 ? 'low' : ''}">${p.confidence.toFixed(1)}</span>
          </div>`;
      }).join('');
      const more = cluster.pairs.length > 6 ? `<div class="muted small" style="margin-top:4px">and ${cluster.pairs.length - 6} more pairs</div>` : '';
      const steps = [
        `<div class="step"><b>${esc(cluster.artists.join(', '))} is linked to ${cluster.events.length} listings on ${esc(day(cluster.event_date))}.</b> An artist can't be in two places at once, so either these are the same event, or one link is wrong.</div>`,
        `<div class="step"><b>The listings:</b><div class="list" style="margin-top:6px">${listingRows}</div></div>`,
        `<div class="step"><b>What each clashing pair says:</b><div class="list" style="margin-top:6px">${pairRows}</div>${more}<div class="muted small" style="margin-top:4px">Hover a listing or a pair to light it up on the graph.</div></div>`,
        `<div class="step"><b>Reading: ${esc(reading.label.toLowerCase())}.</b> ${esc(reading.sentence)}</div>`,
        `<div class="step"><b>Confidence ${cluster.confidence.toFixed(1)}.</b> A rule, not a model: 0.9 at the same venue, 0.8 when several artists are shared, 0.6 otherwise, and 0.3 when a title names a second show.</div>`,
        '<div class="step"><b>Kept apart from the facts.</b> A clash is stored next to what we know, never over it. A duplicate becomes a merge suggestion and a wrong link a correction, both for a person to confirm.</div>',
      ];
      const box = $('#cf-argument');
      box.innerHTML = `<div class="eyebrow">The argument</div><div class="steps">${steps.join('')}</div>`;
      box.onmouseover = (e) => {
        const row = e.target.closest('[data-hl-nodes]');
        if (!row) { this.view.clearHighlight(); return; }
        const nodes = row.dataset.hlNodes.split(' ');
        const pair = row.dataset.hlPair ? row.dataset.hlPair.split('|') : null;
        const edge = pair ? this.view.edgeBetween(pair[0], pair[1]) : null;
        this.view.highlight(nodes, edge ? [edge.id()] : []);
      };
      box.onmouseleave = () => this.view.clearHighlight();
    },
  };

  window.UseCases = window.UseCases || {};
  window.UseCases.conflicts = chapter;
})();
