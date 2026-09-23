'use strict';

// the artist duplicates tab: one act stored as several artist rows, the evidence that ties
// them together drawn as a graph, and the model's score taken apart piece by piece

const LEVEL_TEXT = {
  same_night: [
    ['same venue', 'Played the same venue on the same night'],
    ['same city, other venue', 'Played the same city on the same night'],
    ['different city', 'Played different cities on the same night'],
    ['All other', 'No night in common'],
  ],
  venues: [['>= 3', 'Share 3 or more venues'], ['>= 1', 'Share a venue'], ['All other', 'No venue in common']],
  cities: [['>= 3', 'Share 3 or more cities'], ['>= 1', 'Share a city'], ['All other', 'No city in common']],
  coperformers: [
    ['>= 2', 'Billed with 2 or more of the same artists'],
    ['>= 1', 'Billed with one of the same artists'],
    ['All other', 'No co-performers in common'],
  ],
  similar: [
    ['>= 5', '5 or more of the same similar artists'],
    ['>= 1', 'A similar artist in common'],
    ['All other', 'No similar artists in common'],
  ],
  genres: [['>= 2', '2 or more genres in common'], ['>= 1', 'A genre in common'], ['All other', 'No genre in common']],
  country: [['Exact', 'Same country'], ['All other', 'Different country']],
  activity: [
    ['neither', 'Neither row has events of its own'],
    ['one has no events', 'One row has no events of its own'],
    ['All other', 'Both rows have events of their own'],
  ],
  name: [
    ['numbered', 'One is a numbered namesake, like "Dimitri (1)"'],
    ['name_folded', 'Same name, different capitals'],
    ['accents', 'Same name up to accents or punctuation'],
    ['Exact match on name', 'Identical spelling'],
    ['All other', 'Names differ by a word like "The" or "DJ"'],
  ],
};

const KIND = {
  row: { label: 'artist row', light: '#1b1b18', dark: '#ecece6' },
  night: { label: 'same night, same venue', light: '#d4351c', dark: '#ff6b52' },
  venue: { label: 'shared venue', light: '#b86e00', dark: '#ffb74d' },
  city: { label: 'shared city', light: '#6b6b63', dark: '#9a9a90' },
  coperformer: { label: 'shared co-performer', light: '#0b63ce', dark: '#5aa2ff' },
  similar: { label: 'shared similar artist', light: '#6d3fc0', dark: '#b39ddb' },
};

const artistState = { snapshot: null, byId: new Map(), filter: 'upcoming', query: '', selectedId: null, remembered: null, cy: null };

function levelText(comparison, level) {
  for (const [needle, text] of LEVEL_TEXT[comparison] || []) if (level.includes(needle)) return text;
  return `${comparison}: ${level}`;
}

function odds(points) {
  const factor = 2 ** Math.abs(points);
  const shown = factor >= 1e9 ? 'over a billion' : factor >= 1e6 ? `${Math.round(factor / 1e6)} million` : factor >= 1e3 ? `${Math.round(factor).toLocaleString('en-GB')}` : factor.toFixed(1);
  return `${shown}×`;
}

// tabs --------------------------------------------------------------------

const TAB_COPY = {
  conflicts: ['Conflict worklist', 'Same artist, same night, two listings. Spain, upcoming events.'],
  artists: ['Artist duplicates', 'One act stored as several artist rows, and the evidence that gives it away.'],
};

function showTab(tab) {
  document.body.dataset.view = tab;
  document.querySelectorAll('[data-tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
  const [title, subtitle] = TAB_COPY[tab];
  $('#title').textContent = title;
  $('#subtitle').textContent = subtitle;
  try { localStorage.setItem('ekg:tab', tab); } catch { /* storage may be blocked */ }
  history.replaceState(null, '', tab === 'artists' ? '#artists' : location.pathname + location.search);
  // a graph laid out while its tab was hidden needs its real size again
  if (tab === 'conflicts' && state.cy) { state.cy.resize(); state.cy.fit(undefined, 24); }
  if (tab === 'artists') {
    if (artistState.snapshot && !artistState.selectedId) {
      const start = artistState.byId.has(artistState.remembered) ? artistState.remembered : visibleGroups()[0]?.id;
      selectGroup(start);
    }
    else if (artistState.cy) { artistState.cy.resize(); artistState.cy.fit(undefined, 30); }
  }
}

// list --------------------------------------------------------------------

function groupMatches(group) {
  if (artistState.filter === 'upcoming' && group.upcoming === 0) return false;
  if (artistState.filter === 'admin' && !group.admin_confirmed) return false;
  if (artistState.filter === 'big' && group.size < 3) return false;
  if (!artistState.query) return true;
  const haystack = [...group.members.map((m) => m.name), ...group.shared_venues, ...group.shared_cities].join(' ').toLowerCase();
  return haystack.includes(artistState.query);
}

function visibleGroups() {
  return artistState.snapshot.groups.filter(groupMatches);
}

function renderArtistFilters() {
  const groups = artistState.snapshot.groups;
  const count = (fn) => groups.filter(fn).length;
  const chips = [
    ['upcoming', 'Has upcoming events', count((g) => g.upcoming > 0)],
    ['all', 'All', groups.length],
    ['big', '3 or more rows', count((g) => g.size >= 3)],
    ['admin', 'Confirmed by admins', count((g) => g.admin_confirmed)],
  ];
  $('#a-filters').innerHTML = chips.map(([key, label, n]) => `
    <button class="chip" role="tab" data-afilter="${key}" aria-selected="${artistState.filter === key}">
      ${esc(label)}<span class="n">${n}</span>
    </button>`).join('');
}

function renderArtistList() {
  renderArtistFilters();
  const groups = visibleGroups();
  const shown = groups.slice(0, 400);
  $('#a-groups').innerHTML = groups.length === 0
    ? '<li class="empty">Nothing matches.</li>'
    : shown.map((g) => {
      const where = g.shared_venues.length ? g.shared_venues.slice(0, 2).join(' / ') : g.shared_cities.slice(0, 3).map(titleCase).join(', ');
      return `
        <li class="card" data-gid="${g.id}" aria-current="${g.id === artistState.selectedId}">
          <div class="title">${esc(g.name)}</div>
          <div class="meta">${g.size} rows · ${g.upcoming} upcoming event${g.upcoming === 1 ? '' : 's'}</div>
          <div class="meta">${esc(where || 'no shared venue')}</div>
          <div class="right">
            <span class="conf">${g.explanation ? g.explanation.total.toFixed(1) : ''}</span>
            <span class="pill">${g.size}×</span>
            ${g.admin_confirmed ? '<span class="pill done">admin-confirmed</span>' : ''}
          </div>
        </li>`;
    }).join('') + (groups.length > shown.length ? `<li class="empty">${groups.length - shown.length} more; narrow with the search.</li>` : '');
}

function renderArtistStats() {
  const groups = artistState.snapshot.groups;
  const rows = groups.reduce((n, g) => n + g.size, 0);
  const upcoming = groups.reduce((n, g) => n + g.upcoming, 0);
  $('#a-stats').innerHTML = `
    <div class="stat"><b>${groups.length.toLocaleString('en-GB')}</b><span>acts stored twice+</span></div>
    <div class="stat"><b>${(rows - groups.length).toLocaleString('en-GB')}</b><span>surplus rows</span></div>
    <div class="stat"><b>${upcoming.toLocaleString('en-GB')}</b><span>upcoming events</span></div>
    <div class="stat"><b>≥ 97%</b><span>precise</span></div>`;
}

// detail ------------------------------------------------------------------

function selectGroup(id) {
  if (!artistState.byId.has(id)) return;
  artistState.selectedId = id;
  try { localStorage.setItem('ekg:group', id); } catch { /* storage may be blocked */ }
  renderArtistList();
  renderGroup(artistState.byId.get(id));
  document.querySelector(`.card[data-gid="${id}"]`)?.scrollIntoView({ block: 'nearest' });
}

function memberCard(member) {
  const recent = member.recent.length
    ? `<table class="events">${member.recent.map((e) => `
        <tr>
          <td class="time">${esc(e.date)}</td>
          <td><div class="t">${esc(e.title)}</div><div class="v">${esc(e.venue)} · ${esc(titleCase(e.city))}${e.upcoming ? ' · <b>upcoming</b>' : ''}</div></td>
        </tr>`).join('')}</table>`
    : '<p class="muted-note">No events linked to this row.</p>';
  return `
    <div class="box member${member.keep ? ' keep' : ''}">
      <h3><span>${esc(member.name)}</span> ${member.keep ? '<span class="pill done">keep this row</span>' : '<span class="pill">merge into the kept row</span>'}</h3>
      <div class="member-meta">${member.events} event${member.events === 1 ? '' : 's'} · ${member.upcoming} upcoming · ${esc(member.cities.slice(0, 5).map(titleCase).join(', ') || 'no cities')}</div>
      ${recent}
      <div class="member-id">artist:${esc(member.artist_id)}</div>
    </div>`;
}

function explanationHtml(group) {
  const e = group.explanation;
  if (!e) return '';
  const names = new Map(group.members.map((m) => [m.artist_id, m.name]));
  const widest = Math.max(1, ...e.contributions.map((c) => Math.abs(c.bits)), Math.abs(e.prior));
  const bar = (bits, text, cls) => `
    <div class="wf-row ${cls}">
      <div class="wf-label">${esc(text)}</div>
      <div class="wf-track"><div class="wf-bar ${bits >= 0 ? 'up' : 'down'}" style="width:${(50 * Math.abs(bits)) / widest}%;${bits >= 0 ? 'left:50%' : `right:50%`}"></div></div>
      <div class="wf-value">${bits >= 0 ? '+' : '−'}${Math.abs(bits).toFixed(1)}</div>
    </div>`;
  const shown = e.contributions.filter((c) => Math.abs(c.bits) >= 0.05);
  const verdict = e.total >= 0
    ? `Final score <b>${e.total >= 0 ? '+' : ''}${e.total.toFixed(1)}</b>: the evidence makes one act about <b>${odds(e.total - e.prior)}</b> more likely than it starts out, so the model merges them. People checked 100 merges scored 0 or higher and every one was right.`
    : `Final score <b>${e.total.toFixed(1)}</b>, below the merge line of 0.`;
  return `
    <div class="box explain">
      <h3>Why the model thinks these are one act <span>strongest pair: ${esc(names.get(e.a) || '')} ↔ ${esc(names.get(e.b) || '')}</span></h3>
      <div class="body">
        <p class="wf-intro">Each piece of evidence moves the score. Every +1 point doubles the odds that the two rows are one act, so +10 is about 1,000× more likely; red bars count against.</p>
        ${bar(e.prior, 'Starting point: before any evidence, two artist rows are almost never the same act', 'prior')}
        ${shown.map((c) => bar(c.bits, levelText(c.comparison, c.level), '')).join('')}
        <p class="wf-total">${verdict}</p>
      </div>
    </div>`;
}

function legendHtml(group) {
  const kinds = [...new Set(group.nodes.map((n) => n.kind))];
  const scheme = theme();
  return kinds.map((k) => `<span><i style="background:${KIND[k][scheme]}"></i>${esc(KIND[k].label)}</span>`).join('');
}

function renderGroup(group) {
  const keep = group.members.find((m) => m.keep);
  $('#a-detail').innerHTML = `
    <div class="detail-head">
      <div>
        <h2>${esc(group.name)}</h2>
        <div class="when">${group.size} artist rows for one act · ${group.upcoming} upcoming event${group.upcoming === 1 ? '' : 's'} split across them</div>
      </div>
      <div class="right">
        ${group.explanation ? `<span class="conf">score ${group.explanation.total.toFixed(1)}</span><br>` : ''}
        ${group.admin_confirmed ? '<span class="reading shows">Also confirmed by an admin redirect</span>' : '<span class="reading duplicate_venue">Found by the graph</span>'}
      </div>
    </div>

    <div class="why">
      <h3>What this means</h3>
      <div>TicketSwap has <b>${group.size} separate artist rows</b> for what looks like one act. Keep <b>${esc(keep?.name || '')}</b>, the row most events already point at, and merge the rest into it: lineup names like "${esc(group.name)}" then resolve to one act instead of ${group.size}.</div>
    </div>

    <div class="grid2">
      <div class="box graph-box artist-graph">
        <h3>The evidence, drawn <span>only what two or more rows share</span></h3>
        <div id="a-graph"></div>
        <div class="graph-caption" id="a-caption">Big nodes are the duplicate rows; everything around them is something they have in common. Click a node for details.</div>
        <div class="legend">${legendHtml(group)}</div>
      </div>
      ${explanationHtml(group)}
    </div>

    <div class="members">${group.members.map(memberCard).join('')}</div>`;
  renderArtistGraph(group);
}

function renderArtistGraph(group) {
  if (artistState.cy) artistState.cy.destroy();
  artistState.cy = null;
  const container = $('#a-graph');
  if (!window.cytoscape || !container) return;
  const scheme = theme();
  const text = getComputedStyle(document.documentElement).getPropertyValue('--text').trim();
  const panel = getComputedStyle(document.documentElement).getPropertyValue('--panel').trim();
  const ok = getComputedStyle(document.documentElement).getPropertyValue('--ok').trim();
  const elements = [
    ...group.nodes.map((n, i) => ({
      data: {
        id: `n${i}`,
        label: n.label.length > 30 ? `${n.label.slice(0, 29)}…` : n.label,
        full: n.label,
        kind: n.kind,
        color: KIND[n.kind][scheme],
      },
      classes: [n.kind, n.keep ? 'keep' : ''].join(' '),
    })),
    ...group.edges.map(([source, target], i) => ({
      data: { id: `e${i}`, source: `n${source}`, target: `n${target}`, kind: group.nodes[target].kind, color: KIND[group.nodes[target].kind][scheme] },
    })),
  ];
  artistState.cy = cytoscape({
    container,
    elements,
    style: [
      { selector: 'node', style: { 'background-color': 'data(color)', label: 'data(label)', color: text, 'font-size': 9, 'text-valign': 'bottom', 'text-margin-y': 3, width: 14, height: 14, 'text-wrap': 'ellipsis', 'text-max-width': 130, 'text-background-color': panel, 'text-background-opacity': 0.85, 'text-background-padding': 2, 'text-background-shape': 'roundrectangle' } },
      { selector: 'node.row', style: { width: 34, height: 34, 'font-size': 12, 'font-weight': 700, shape: 'round-rectangle', 'background-color': text, color: text } },
      { selector: 'node.keep', style: { 'border-width': 4, 'border-color': ok } },
      { selector: 'node.night', style: { shape: 'star', width: 20, height: 20 } },
      { selector: 'edge', style: { width: 1.6, 'line-color': 'data(color)', opacity: 0.7, 'curve-style': 'bezier' } },
      { selector: 'edge[kind = "night"]', style: { width: 3, opacity: 1 } },
      { selector: ':selected', style: { 'overlay-opacity': 0.12, 'overlay-color': panel } },
    ],
    layout: {
      name: 'concentric',
      concentric: (node) => (node.data('kind') === 'row' ? 2 : 1),
      levelWidth: () => 1,
      minNodeSpacing: 30,
      padding: 30,
      animate: false,
    },
  });
  const caption = $('#a-caption');
  artistState.cy.on('tap', 'node', (evt) => {
    const d = evt.target.data();
    const rows = evt.target.connectedEdges().length;
    caption.innerHTML = d.kind === 'row'
      ? `<b>${esc(d.full)}</b> · one of the duplicate artist rows${evt.target.hasClass('keep') ? ', the one to keep' : ''}`
      : `<b>${esc(d.full)}</b> · ${esc(KIND[d.kind].label)}, shared by ${rows} of the rows`;
  });
}

function renderArtistFooter() {
  const s = artistState.snapshot;
  const exported = new Date(s.exported_at).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
  $('#a-foot').innerHTML = `
    <span>Snapshot ${esc(exported)} · ${s.groups.length.toLocaleString('en-GB')} groups</span>
    <span><kbd>j</kbd>/<kbd>k</kbd> move · <kbd>/</kbd> search</span>
    <button id="a-method">How duplicates are found</button>`;
  $('#a-method').addEventListener('click', () => alert(s.method.join('\n\n')));
}

// wiring ------------------------------------------------------------------

function moveGroup(step) {
  const groups = visibleGroups();
  if (groups.length === 0) return;
  const index = groups.findIndex((g) => g.id === artistState.selectedId);
  selectGroup(groups[Math.min(groups.length - 1, Math.max(0, index + step))].id);
}

function wireArtists() {
  document.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab)));
  $('#a-filters').addEventListener('click', (evt) => {
    const chip = evt.target.closest('[data-afilter]');
    if (!chip) return;
    artistState.filter = chip.dataset.afilter;
    renderArtistList();
  });
  $('#a-search').addEventListener('input', (evt) => {
    artistState.query = evt.target.value.trim().toLowerCase();
    renderArtistList();
  });
  $('#a-groups').addEventListener('click', (evt) => {
    const card = evt.target.closest('[data-gid]');
    if (card) selectGroup(card.dataset.gid);
  });
  document.addEventListener('keydown', (evt) => {
    if (document.body.dataset.view !== 'artists') return;
    const typing = evt.target.matches('input, textarea');
    if (evt.key === 'Escape' && typing) { evt.target.blur(); return; }
    if (typing || evt.metaKey || evt.ctrlKey || evt.altKey) return;
    if (evt.key === 'j' || evt.key === 'ArrowDown') { evt.preventDefault(); moveGroup(1); }
    else if (evt.key === 'k' || evt.key === 'ArrowUp') { evt.preventDefault(); moveGroup(-1); }
    else if (evt.key === '/') { evt.preventDefault(); $('#a-search').focus(); }
  });
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    const group = artistState.byId.get(artistState.selectedId);
    if (group && document.body.dataset.view === 'artists') renderGroup(group);
  });
}

async function mainArtists() {
  wireArtists();
  let tab = 'conflicts';
  try { tab = localStorage.getItem('ekg:tab') || 'conflicts'; } catch { /* storage may be blocked */ }
  if (location.hash === '#artists') tab = 'artists';
  if (!(tab in TAB_COPY)) tab = 'conflicts';
  try { artistState.remembered = localStorage.getItem('ekg:group'); } catch { /* storage may be blocked */ }
  try {
    const response = await fetch('data/artists.json');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    artistState.snapshot = await response.json();
  } catch (error) {
    $('#a-detail').innerHTML = `<div class="empty">Could not load data/artists.json (${esc(error.message)}). Run <code>uv run python disco/export_artist_groups.py</code> and redeploy.</div>`;
    showTab(tab);
    return;
  }
  artistState.byId = new Map(artistState.snapshot.groups.map((g) => [g.id, g]));
  renderArtistStats();
  renderArtistFooter();
  renderArtistList();
  showTab(tab);
}

mainArtists();
