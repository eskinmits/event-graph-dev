/* use case 2: one act stored as several artist rows, the evidence that ties them together, and the score taken apart */
(function () {
  const { $, $$, esc, fmt } = EKG;

  // the plain-english line for each comparison level, and the graph nodes it points at
  const LEVEL_TEXT = {
    same_night: [
      ['same venue', 'Played the same venue on the same night'],
      ['same city, other venue', 'Played the same city on the same night'],
      ['different city', 'Played different cities on the same night'],
      ['All other', 'No night in common'],
    ],
    venues: [['>= 3', 'Share 3 or more venues'], ['>= 1', 'Share a venue'], ['All other', 'No venue in common']],
    cities: [['>= 3', 'Share 3 or more cities'], ['>= 1', 'Share a city'], ['All other', 'No city in common']],
    coperformers: [['>= 2', 'Billed with 2 or more of the same artists'], ['>= 1', 'Billed with one of the same artists'], ['All other', 'No co-performers in common']],
    similar: [['>= 5', '5 or more of the same similar artists'], ['>= 1', 'A similar artist in common'], ['All other', 'No similar artists in common']],
    genres: [['>= 2', '2 or more genres in common'], ['>= 1', 'A genre in common'], ['All other', 'No genre in common']],
    country: [['Exact', 'Same country'], ['All other', 'Different country']],
    activity: [['neither', 'Neither row has events of its own'], ['one has no events', 'One row has no events of its own'], ['All other', 'Both rows have events of their own']],
    name: [
      ['numbered', 'One is a numbered namesake, like “Dimitri (1)”'],
      ['name_folded', 'Same name, different capitals'],
      ['accents', 'Same name up to accents or punctuation'],
      ['Exact match on name', 'Identical spelling'],
      ['All other', 'Names differ by a word like “The” or “DJ”'],
    ],
  };
  const COMPARISON_KIND = { same_night: 'night', venues: 'venue', cities: 'city', coperformers: 'coperformer', similar: 'similar' };
  // each shared thing is drawn as the graph type it really is, so colours mean the same as in ekg-explore
  const KIND = {
    row: { type: 'artist', verb: '' },
    night: { type: 'event', verb: 'played this night, at this venue', predicate: 'performs_at', cls: 'path' },
    venue: { type: 'venue', verb: 'played at', predicate: 'held_at' },
    city: { type: 'city', verb: 'played in', predicate: 'in_city' },
    coperformer: { type: 'artist', verb: 'shared a bill with', predicate: 'performs_at' },
    similar: { type: 'artist', verb: 'sounds like (Chartmetric)', predicate: 'similar_to' },
  };
  const FILTERS = [['upcoming', 'Has upcoming events'], ['all', 'All'], ['big', '3 or more rows'], ['admin', 'Confirmed by admins']];

  const titleCase = (text) => String(text || '').replace(/(^|[\s\-/])(\p{L})/gu, (_, sep, ch) => sep + ch.toUpperCase());
  const signed = (x) => `${x >= 0 ? '+' : '−'}${Math.abs(x).toFixed(1)}`;

  function levelText(comparison, level) {
    for (const [needle, text] of LEVEL_TEXT[comparison] || []) if (level.includes(needle)) return text;
    return `${comparison}: ${level}`;
  }

  function odds(points) {
    const factor = 2 ** Math.abs(points);
    if (factor >= 1e9) return 'over a billion';
    if (factor >= 1e6) return `${Math.round(factor / 1e6)} million`;
    return fmt(Math.round(factor));
  }

  const chapter = {
    id: 'artists',
    data: null,
    byId: new Map(),
    filter: 'upcoming',
    query: '',
    current: null,
    view: null,

    async load() {
      if (this.data) return;
      const response = await fetch('data/artists.json');
      if (!response.ok) throw new Error(`could not load data/artists.json (${response.status})`);
      this.data = await response.json();
      this.byId = new Map(this.data.groups.map((g) => [g.id, g]));
    },

    init() {
      this.view = new EKG.GraphView($('#ad-cy'));
      $('#ad-search').addEventListener('input', (e) => { this.query = e.target.value.trim().toLowerCase(); this.renderList(); });
      $('#ad-filter').addEventListener('click', (e) => {
        const b = e.target.closest('[data-f]');
        if (!b) return;
        this.filter = b.dataset.f;
        this.renderFilter();
        this.renderList();
      });
      $('#ad-list').addEventListener('click', (e) => {
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
      return this.data.groups.filter((g) => {
        if (this.filter === 'upcoming' && g.upcoming === 0) return false;
        if (this.filter === 'admin' && !g.admin_confirmed) return false;
        if (this.filter === 'big' && g.size < 3) return false;
        if (!this.query) return true;
        return [...g.members.map((m) => m.name), ...g.shared_venues, ...g.shared_cities].join(' ').toLowerCase().includes(this.query);
      });
    },

    renderIntro() {
      const groups = this.data.groups;
      const rows = groups.reduce((n, g) => n + g.size, 0);
      const upcoming = groups.reduce((n, g) => n + g.upcoming, 0);
      $('#ad-lede').innerHTML = `<b>${fmt(groups.length)}</b> acts have more than one artist row: <b>${fmt(rows - groups.length)}</b> rows too many, splitting <b>${fmt(upcoming)}</b> upcoming events between copies of the same act.`;
      $('#ad-callout').innerHTML = '<b>Checked by people:</b> of 100 merges the model makes, all 100 were the same act (at least 97% precise), and it finds 9 in 10 of the duplicates admins had already merged.';
    },

    renderFilter() {
      const groups = this.data.groups;
      const count = { upcoming: groups.filter((g) => g.upcoming > 0).length, all: groups.length, big: groups.filter((g) => g.size >= 3).length, admin: groups.filter((g) => g.admin_confirmed).length };
      $('#ad-filter').innerHTML = FILTERS.map(([key, label]) => `<button data-f="${key}" class="${this.filter === key ? 'on' : ''}">${esc(label)} <span class="muted">${fmt(count[key])}</span></button>`).join('');
    },

    renderList() {
      const rows = this.visible();
      $('#ad-list').innerHTML = rows.length === 0
        ? '<p class="muted">Nothing matches.</p>'
        : rows.slice(0, 250).map((g) => `
          <button class="row ${this.current === g.id ? 'on' : ''}" data-id="${esc(g.id)}">
            ${EKG.typeDot('artist')}
            <div class="main">
              <div class="title">${esc(g.name)}</div>
              <div class="sub">${g.size} rows · ${fmt(g.upcoming)} upcoming${g.admin_confirmed ? ' · admin-confirmed' : ''}</div>
            </div>
            <span class="conf-pill">${g.explanation ? signed(g.explanation.total) : ''}</span>
          </button>`).join('') + (rows.length > 250 ? `<p class="muted small">${fmt(rows.length - 250)} more; narrow with the search.</p>` : '');
    },

    select(id) {
      const group = this.byId.get(id);
      if (!group) return;
      this.current = id;
      $$('#ad-list .row').forEach((r) => r.classList.toggle('on', r.dataset.id === id));
      $(`#ad-list .row[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest' });
      this.renderGraph(group);
      this.renderArgument(group);
    },

    move(step) {
      const rows = this.visible();
      if (!rows.length) return;
      const i = rows.findIndex((g) => g.id === this.current);
      this.select(rows[Math.max(0, Math.min(rows.length - 1, i + step))].id);
    },

    nodeId(group, index) {
      return `${KIND[group.nodes[index].kind].type}:${group.id}:${index}`;
    },

    renderGraph(group) {
      const rowNames = group.nodes.map((n) => n.label);
      const nodes = group.nodes.map((n, i) => {
        const kind = KIND[n.kind];
        const type = EKG.TYPE[kind.type];
        const holders = group.edges.filter(([, target]) => target === i).length;
        const tip = n.kind === 'row'
          ? `<div class="tt-type" style="color:var(--t-artist)">Artist row${n.keep ? ' · keep this one' : ''}</div><b>${esc(n.label)}</b>`
          : `<div class="tt-type" style="color:${type.color}">${esc(type.name)}</div><b>${esc(n.label)}</b><div class="muted">shared by ${holders} of the rows</div>`;
        return {
          node_id: this.nodeId(group, i),
          label: n.label.length > 26 ? `${n.label.slice(0, 25)}…` : n.label,
          role: n.kind === 'row' ? 'target' : '',
          cls: n.keep ? 'true' : '',
          size: n.kind === 'row' ? undefined : n.kind === 'night' ? 26 : n.kind === 'city' ? 18 : 22,
          tip,
        };
      });
      const edges = group.edges.map(([source, target]) => {
        const kind = KIND[group.nodes[target].kind];
        return {
          source: this.nodeId(group, source),
          target: this.nodeId(group, target),
          predicate: kind.predicate,
          confidence: 0.6,
          cls: kind.cls || '',
          tip: `<b>${esc(rowNames[source])}</b> ${esc(kind.verb)} <b>${esc(rowNames[target])}</b>`,
        };
      });
      this.view.set(nodes, edges, { layout: 'cose' });
      const kinds = new Set(group.nodes.map((n) => n.kind));
      const types = [...new Set([...kinds].map((k) => KIND[k].type))];
      const extras = kinds.has('night') ? [['path', 'same night, same venue']] : [];
      $('#ad-legend').innerHTML = EKG.legendHTML(types, extras);
      $('#ad-title').innerHTML = `<b>${esc(group.name)}</b> · ${group.size} artist rows<br><span class="muted">White rings are the rows, green is the one to keep. Only what two or more rows share is drawn; hover a reason on the right to light it up.</span>`;
    },

    renderArgument(group) {
      const keep = group.members.find((m) => m.keep) || group.members[0];
      const e = group.explanation;
      const reasons = e
        ? e.contributions.filter((c) => Math.abs(c.bits) >= 0.05).map((c) => `
          <div class="row static" data-kind="${esc(COMPARISON_KIND[c.comparison] || '')}">
            <div class="main"><div class="title">${esc(levelText(c.comparison, c.level))}</div></div>
            <span class="end ${c.bits >= 0 ? 'pos' : 'neg'}">${signed(c.bits)}</span>
          </div>`).join('')
        : '';
      const members = group.members.map((m) => `
        <div class="member ${m.keep ? 'keep' : ''}">
          <div class="member-head"><b>${esc(m.name)}</b>${m.keep ? '<span class="conf-pill">keep</span>' : '<span class="conf-pill low">merge</span>'}</div>
          <div class="muted small">${fmt(m.events)} events · ${fmt(m.upcoming)} upcoming · ${esc(m.cities.slice(0, 4).map(titleCase).join(', ') || 'no cities')}</div>
          ${m.recent.slice(0, 3).map((r) => `<div class="member-event"><span>${esc(EKG.dateLabel(r.date))}</span> ${esc(r.title)}<span class="muted"> · ${esc(r.venue)}</span></div>`).join('')}
        </div>`).join('');
      const steps = [
        `<div class="step"><b>${group.size} artist rows for one act.</b> Keep <b>${esc(keep.name)}</b>, the row ${fmt(keep.events)} events already point at, and merge the other${group.size > 2 ? 's' : ''} into it. Lineup names like “${esc(group.name)}” then find one act instead of ${group.size}.</div>`,
        e ? `<div class="step"><b>The graph backs it up:</b>
            <div class="list" style="margin-top:6px">
              <div class="row static"><div class="main"><div class="title muted">Starting point, before any evidence</div></div><span class="end neg">${signed(e.prior)}</span></div>
              ${reasons}
            </div>
            <div class="muted small" style="margin-top:4px">Every +1 doubles the odds that the rows are one act. Hover a reason to light it up on the graph.</div></div>` : '',
        e ? `<div class="step"><b>Score ${signed(e.total)}.</b> Together the evidence makes “one act” about <b>${odds(e.total - e.prior)}×</b> more likely than it starts out${e.total >= 0 ? ', so the model merges them' : ''}. People checked 100 merges scored 0 or higher, and every one was right.</div>` : '',
        `<div class="step"><b>The rows:</b><div class="members">${members}</div></div>`,
        `<div class="step"><b>Kept apart from the facts.</b> This is a proposal: nothing has been merged in TicketSwap.${group.admin_confirmed ? ' An admin has already merged listings that carry two of these rows, which is how this group was confirmed.' : ''} A merge by an admin, or a same-as link stored beside the facts that can be undone in one step, would make it real.</div>`,
      ].filter(Boolean);
      const box = $('#ad-argument');
      box.innerHTML = `<div class="eyebrow">The argument</div><div class="steps">${steps.join('')}</div>`;
      box.onmouseover = (ev) => {
        const row = ev.target.closest('[data-kind]');
        const kind = row?.dataset.kind;
        if (!kind) { this.view.clearHighlight(); return; }
        const targets = group.nodes.map((n, i) => (n.kind === kind ? i : -1)).filter((i) => i >= 0);
        const lit = new Set([...targets, ...group.nodes.map((n, i) => (n.kind === 'row' ? i : -1)).filter((i) => i >= 0)]);
        const edgeIds = group.edges.filter(([, t]) => targets.includes(t)).map(([s, t]) => this.view.edgeBetween(this.nodeId(group, s), this.nodeId(group, t))?.id()).filter(Boolean);
        this.view.highlight([...lit].map((i) => this.nodeId(group, i)), edgeIds);
      };
      box.onmouseleave = () => this.view.clearHighlight();
    },
  };

  window.UseCases = window.UseCases || {};
  window.UseCases.artists = chapter;
})();
