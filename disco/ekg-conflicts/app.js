'use strict';

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
    sentence: 'The same artist in two cities on one night. One artist link is probably wrong: a tribute show, a namesake, a title match gone astray.',
  },
  two_shows: {
    label: 'Two shows',
    sentence: 'A title names a numbered session ("segundo pase"), so this is probably a genuine second show. Kept at low confidence in case one of them is itself a duplicate.',
  },
};

const VERDICTS = [
  { key: 'duplicate', label: 'Duplicate', hint: 'one event listed twice', shortcut: '1' },
  { key: 'wrong_link', label: 'Wrong artist link', hint: 'an artist is linked in error', shortcut: '2' },
  { key: 'genuine', label: 'Genuine', hint: 'both events are real', shortcut: '3' },
  { key: 'unsure', label: 'Not sure', hint: 'needs a second look', shortcut: '4' },
];
const VERDICT_LABEL = Object.fromEntries(VERDICTS.map((v) => [v.key, v.label]));

const NODE_COLORS = {
  light: {
    event: '#0b63ce', artist: '#d4351c', venue: '#b86e00', city: '#6b6b63', genre: '#2e7d32',
    external_event: '#6d3fc0', organizer_brand: '#00838f', organizer_company: '#00838f',
    ticket_provider: '#ad1457', series: '#5d4037',
  },
  dark: {
    event: '#5aa2ff', artist: '#ff6b52', venue: '#ffb74d', city: '#9a9a90', genre: '#81c784',
    external_event: '#b39ddb', organizer_brand: '#4dd0e1', organizer_company: '#4dd0e1',
    ticket_provider: '#f48fb1', series: '#bcaaa4',
  },
};

const PREDICATE_TEXT = {
  performs_at: 'performs at', held_at: 'held at', in_city: 'in city', has_genre: 'has genre',
  imported_as: 'imported as', same_as: 'same as (redirect)', promoted_by: 'promoted by',
  sold_via: 'sold via', series_of: 'series of', conflicts_with: 'conflicts with',
};

const state = {
  snapshot: null,
  byId: new Map(),
  filter: 'all',
  query: '',
  selectedId: null,
  verdicts: new Map(),
  me: null,
  myCid: null,
  others: new Map(),
  cy: null,
  collection: null,
  room: null,
};

const $ = (selector) => document.querySelector(selector);

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

function titleCase(text) {
  return String(text || '').replace(/(^|[\s\-/])(\p{L})/gu, (_, sep, ch) => sep + ch.toUpperCase());
}

function formatDate(isoDate) {
  return new Date(`${isoDate}T12:00:00`).toLocaleDateString('en-GB', {
    weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
  });
}

function initials(name) {
  return String(name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join('');
}

function theme() {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function toast(message) {
  let el = $('.toast');
  if (!el) {
    el = document.createElement('div');
    el.className = 'toast';
    document.body.append(el);
  }
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove('show'), 2200);
}

// verdicts ----------------------------------------------------------------

function currentVerdict(clusterId) {
  const docs = state.verdicts.get(clusterId);
  if (!docs || docs.length === 0) return null;
  return docs.reduce((latest, doc) => (doc.updatedAt > latest.updatedAt ? doc : latest));
}

function upsertVerdictDoc(doc) {
  const docs = (state.verdicts.get(doc.clusterId) || []).filter((d) => d.id !== doc.id);
  docs.push(doc);
  state.verdicts.set(doc.clusterId, docs);
}

function removeVerdictDoc(id) {
  for (const [clusterId, docs] of state.verdicts) {
    const kept = docs.filter((d) => d.id !== id);
    if (kept.length !== docs.length) state.verdicts.set(clusterId, kept);
  }
}

async function loadVerdicts() {
  const pageSize = 1000;
  for (let offset = 0; ; offset += pageSize) {
    const page = await state.collection.list({ limit: pageSize, offset });
    page.forEach(upsertVerdictDoc);
    if (page.length < pageSize) break;
  }
}

async function recordVerdict(verdictKey) {
  const cluster = state.byId.get(state.selectedId);
  if (!cluster) return;
  if (!state.collection || !state.me) {
    toast('Verdicts need the Disco backend; open the deployed app or disco preview.');
    return;
  }
  const note = $('#note')?.value.trim() || '';
  const mine = (state.verdicts.get(cluster.id) || []).find((d) => d.byId === state.me.id);
  const fields = {
    clusterId: cluster.id,
    verdict: verdictKey,
    note,
    byId: state.me.id,
    byName: state.me.name,
    reading: cluster.reading,
    confidence: cluster.confidence,
    eventIds: cluster.events.map((e) => e.node_id),
  };
  try {
    const saved = mine
      ? await state.collection.update(mine.id, fields)
      : await state.collection.create(fields);
    upsertVerdictDoc(saved);
    toast(`Marked ${VERDICT_LABEL[verdictKey].toLowerCase()}`);
    renderList();
    renderVerdict(cluster);
    renderStats();
  } catch (error) {
    console.error('verdict save failed', error);
    toast('Could not save the verdict. Try again.');
  }
}

// presence ----------------------------------------------------------------

function joinRoom() {
  const room = disco.channel('ekg-conflicts-reviewers');
  state.room = room;
  room.on('presence', ({ ev, who, members }) => {
    if (ev === 'you') state.myCid = who.cid;
    const live = new Set(members.map((m) => m.cid));
    for (const cid of state.others.keys()) if (!live.has(cid)) state.others.delete(cid);
    for (const member of members) {
      if (member.cid === state.myCid) continue;
      const known = state.others.get(member.cid) || {};
      state.others.set(member.cid, { ...known, name: member.name });
    }
    if (ev === 'join') announceViewing();
    renderPeople();
    renderList();
  });
  room.on('message', (data, from) => {
    if (!data || typeof data !== 'object' || from.cid === state.myCid) return;
    const known = state.others.get(from.cid) || { name: from.name };
    state.others.set(from.cid, { ...known, name: from.name, viewing: data.viewing ?? null });
    renderList();
  });
}

function announceViewing() {
  if (state.room) state.room.send({ viewing: state.selectedId });
}

function otherPeople() {
  // one person with several tabs open is one reviewer, and my own other tabs are not "others"
  const byName = new Map();
  for (const other of state.others.values()) {
    if (!other.name || other.name === state.me?.name) continue;
    byName.set(other.name, { ...byName.get(other.name), ...other });
  }
  return [...byName.values()];
}

function renderPeople() {
  const people = otherPeople();
  const me = state.me ? [{ name: state.me.name, avatar: state.me.avatar, self: true }] : [];
  $('#people').innerHTML = [...me, ...people].slice(0, 8).map((p) => `
    <span class="avatar" title="${esc(p.name)}${p.self ? ' (you)' : ''}">
      ${p.avatar ? `<img src="${esc(p.avatar)}" alt="">` : esc(initials(p.name))}
    </span>`).join('');
}

// list --------------------------------------------------------------------

function matchesFilter(cluster) {
  if (state.filter === 'unreviewed' && currentVerdict(cluster.id)) return false;
  if (state.filter in READINGS && cluster.reading !== state.filter) return false;
  if (!state.query) return true;
  const haystack = [
    ...cluster.artists,
    ...cluster.events.flatMap((e) => [e.title, e.venue_name, e.city_name]),
  ].join(' ').toLowerCase();
  return haystack.includes(state.query);
}

function visibleClusters() {
  return state.snapshot.clusters.filter(matchesFilter);
}

function renderFilters() {
  const clusters = state.snapshot.clusters;
  const count = (fn) => clusters.filter(fn).length;
  const chips = [
    ['all', 'All', clusters.length],
    ['unreviewed', 'Unreviewed', count((c) => !currentVerdict(c.id))],
    ...Object.entries(READINGS).map(([key, r]) => [key, r.label, count((c) => c.reading === key)]),
  ];
  $('#filters').innerHTML = chips.map(([key, label, n]) => `
    <button class="chip" role="tab" data-filter="${key}" aria-selected="${state.filter === key}">
      ${esc(label)}<span class="n">${n}</span>
    </button>`).join('');
}

function renderList() {
  renderFilters();
  const viewers = new Map();
  for (const other of otherPeople()) {
    if (!other.viewing) continue;
    viewers.set(other.viewing, [...(viewers.get(other.viewing) || []), other.name]);
  }
  const clusters = visibleClusters();
  $('#clusters').innerHTML = clusters.length === 0
    ? '<li class="empty">Nothing matches.</li>'
    : clusters.map((c) => {
      const verdict = currentVerdict(c.id);
      const venues = [...new Set(c.events.map((e) => e.venue_name))];
      const watching = viewers.get(c.id) || [];
      return `
        <li class="card" data-id="${c.id}" aria-current="${c.id === state.selectedId}">
          <div class="title">${esc(c.artists.join(', '))}</div>
          <div class="meta">${esc(formatDate(c.event_date))} · ${c.events.length} listings · ${esc(titleCase(c.events[0]?.city_name))}</div>
          <div class="meta">${esc(venues.slice(0, 2).join(' / '))}${venues.length > 2 ? ` +${venues.length - 2}` : ''}</div>
          <div class="right">
            <span class="conf">${c.confidence.toFixed(1)}</span>
            <span class="reading ${c.reading}">${esc(READINGS[c.reading].label)}</span>
            ${verdict ? `<span class="pill done">${esc(VERDICT_LABEL[verdict.verdict])}</span>` : ''}
            ${watching.length ? `<span class="pill" title="${esc(watching.join(', '))} looking">${esc(watching.map(initials).join(' '))} 👀</span>` : ''}
          </div>
        </li>`;
    }).join('');
}

function renderStats() {
  const clusters = state.snapshot.clusters;
  const reviewed = clusters.filter((c) => currentVerdict(c.id)).length;
  const pairs = clusters.reduce((n, c) => n + c.pairs.length, 0);
  const listings = clusters.reduce((n, c) => n + c.events.length, 0);
  $('#stats').innerHTML = `
    <div class="stat"><b>${clusters.length}</b><span>clusters</span></div>
    <div class="stat"><b>${listings}</b><span>listings</span></div>
    <div class="stat"><b>${pairs}</b><span>conflict edges</span></div>
    <div class="stat"><b>${reviewed}</b><span>reviewed</span></div>`;
}

// detail ------------------------------------------------------------------

function select(id) {
  if (!state.byId.has(id)) return;
  state.selectedId = id;
  try { localStorage.setItem('ekg-conflicts:selected', id); } catch { /* storage may be blocked */ }
  renderList();
  renderDetail(state.byId.get(id));
  document.querySelector(`.card[data-id="${id}"]`)?.scrollIntoView({ block: 'nearest' });
  announceViewing();
}

function renderDetail(cluster) {
  const reading = READINGS[cluster.reading];
  const titleOf = new Map(cluster.events.map((e) => [e.node_id, e]));
  const artistLabel = new Map(cluster.nodes.map((n) => [n.id, n.label]));
  const pairLines = cluster.pairs.map((p) => {
    const a = titleOf.get(p.src);
    const b = titleOf.get(p.dst);
    const facts = [
      p.is_same_venue ? 'same venue' : 'different venues',
      `shares ${p.shared_artists.map((id) => artistLabel.get(id) || id).join(', ')}`,
      p.is_possible_second_session ? 'a title names a second session' : null,
    ].filter(Boolean).join(' · ');
    return `<li>${listingName(a)} ↔ ${listingName(b)}: ${esc(facts)} <span class="conf">${p.confidence.toFixed(1)}</span></li>`;
  });
  const shownPairs = pairLines.slice(0, 4).join('');
  const hiddenPairs = pairLines.length > 4
    ? `<details><summary>${pairLines.length - 4} more pair${pairLines.length - 4 === 1 ? '' : 's'}</summary><ul>${pairLines.slice(4).join('')}</ul></details>`
    : '';

  $('#detail').innerHTML = `
    <div class="detail-head">
      <div>
        <h2>${esc(cluster.artists.join(', '))}</h2>
        <div class="when">${esc(formatDate(cluster.event_date))} · ${cluster.events.length} listings · ${cluster.pairs.length} conflict edge${cluster.pairs.length === 1 ? '' : 's'}</div>
      </div>
      <div class="right">
        <span class="conf">confidence ${cluster.confidence.toFixed(1)}</span><br>
        <span class="reading ${cluster.reading}">${esc(reading.label)}</span>
      </div>
    </div>

    <div class="why">
      <h3>Why this was flagged</h3>
      <div>${esc(reading.sentence)}</div>
      <ul>${shownPairs}</ul>${hiddenPairs}
      <div class="rule">Rule-based reading from derived <code>conflicts_with</code> edges, not a model prediction. An artist cannot play two places at once, so every pair is a duplicate, a wrong link, or two genuine shows.</div>
    </div>

    <div class="grid2">
      <div class="box">
        <h3>Listings <span>times in Europe/Madrid</span></h3>
        <table class="events">
          ${cluster.events.map((e) => `
            <tr>
              <td class="time">${esc(e.starts_at.slice(11))}</td>
              <td>
                <div class="t">${esc(e.title)}</div>
                <div class="v">${esc(e.venue_name)} · ${esc(titleCase(e.city_name))} · ${esc(e.country_iso)}${e.category ? ` · ${esc(e.category)}` : ''}</div>
              </td>
              <td class="links">
                ${e.frontend_url ? `<a href="${esc(e.frontend_url)}" target="_blank" rel="noopener">page</a>` : ''}
                ${e.admin_url ? `<a href="${esc(e.admin_url)}" target="_blank" rel="noopener">admin</a>` : ''}
              </td>
            </tr>`).join('')}
        </table>
      </div>

      <div class="box verdict">
        <h3>Your verdict <span>saved for everyone, becomes an eval label</span></h3>
        <div class="body" id="verdict"></div>
      </div>
    </div>

    <div class="box graph-box">
      <h3>Neighbourhood <span>one hop from each listing, from the Spain slice</span></h3>
      <div id="graph"></div>
      <div class="graph-caption" id="caption">Click a node or edge to see what it is and where it came from.</div>
      <div class="legend">${legend(cluster)}</div>
    </div>`;

  renderGraph(cluster);
  renderVerdict(cluster);
}

function listingName(event) {
  if (!event) return '<b>?</b>';
  return `<b>${esc(event.title)}</b> <span class="rule">(${esc(event.starts_at.slice(11))}, ${esc(event.venue_name)})</span>`;
}

function legend(cluster) {
  const colors = NODE_COLORS[theme()];
  const types = [...new Set(cluster.nodes.map((n) => n.type))];
  const items = types.map((t) => `<span><i style="background:${colors[t] || '#888'}"></i>${esc(t.replace('_', ' '))}</span>`);
  items.push(`<span><i style="background:transparent;border:2px dashed var(--conflict);border-radius:2px"></i>conflicts with</span>`);
  return items.join('');
}

function renderVerdict(cluster) {
  const el = $('#verdict');
  if (!el) return;
  const docs = [...(state.verdicts.get(cluster.id) || [])].sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1));
  const mine = state.me ? docs.find((d) => d.byId === state.me.id) : null;
  const draft = $('#note')?.value;
  el.innerHTML = `
    <div class="choices">
      ${VERDICTS.map((v) => `
        <button class="choice" data-verdict="${v.key}" aria-pressed="${mine?.verdict === v.key}">
          <kbd>${v.shortcut}</kbd>${esc(v.label)}<small>${esc(v.hint)}</small>
        </button>`).join('')}
    </div>
    <textarea class="note" id="note" rows="1" placeholder="Optional note: which listing to keep, which link is wrong…">${esc(draft ?? mine?.note ?? '')}</textarea>
    <ul class="history">
      ${docs.length === 0 ? '<li>No verdicts yet.</li>' : docs.map((d) => `
        <li><b>${esc(d.byName)}</b>: ${esc(VERDICT_LABEL[d.verdict] || d.verdict)}${d.note ? ` · “${esc(d.note)}”` : ''} · ${esc(new Date(d.updatedAt).toLocaleString('en-GB', { dateStyle: 'short', timeStyle: 'short' }))}</li>`).join('')}
    </ul>`;
}

function renderGraph(cluster) {
  if (state.cy) state.cy.destroy();
  const container = $('#graph');
  if (!window.cytoscape || !container) return;
  const colors = NODE_COLORS[theme()];
  const text = getComputedStyle(document.documentElement).getPropertyValue('--text').trim();
  const conflict = getComputedStyle(document.documentElement).getPropertyValue('--conflict').trim();
  const listingIds = new Set(cluster.events.map((e) => e.node_id));
  const sharedArtists = new Set(cluster.pairs.flatMap((p) => p.shared_artists));
  const known = new Set(cluster.nodes.map((n) => n.id));

  const elements = [
    ...cluster.nodes.map((n) => ({
      data: {
        id: n.id,
        label: n.label.length > 28 ? `${n.label.slice(0, 27)}…` : n.label,
        full: n.label,
        type: n.type,
        degree: n.degree,
        color: colors[n.type] || '#888',
      },
      classes: [listingIds.has(n.id) ? 'listing' : '', sharedArtists.has(n.id) ? 'shared' : ''].join(' '),
    })),
    ...cluster.edges
      .filter((e) => known.has(e.source) && known.has(e.target))
      .map((e, i) => ({ data: { id: `e${i}`, ...e, kind: 'source' } })),
    ...cluster.pairs.map((p, i) => ({
      data: { id: `c${i}`, source: p.src, target: p.dst, predicate: 'conflicts_with', provenance: 'derived_conflict', source_class: 'derived', confidence: p.confidence, kind: 'conflict' },
    })),
  ];

  state.cy = cytoscape({
    container,
    elements,
    style: [
      { selector: 'node', style: {
        'background-color': 'data(color)', label: 'data(label)', color: text,
        'font-size': 9, 'text-valign': 'bottom', 'text-margin-y': 3, width: 14, height: 14,
        'text-wrap': 'ellipsis', 'text-max-width': 120,
      } },
      { selector: 'node.listing', style: { width: 26, height: 26, 'font-size': 10, 'font-weight': 700, shape: 'round-rectangle' } },
      { selector: 'node.shared', style: { width: 24, height: 24, 'border-width': 3, 'border-color': conflict, 'font-weight': 700 } },
      { selector: 'edge', style: { width: 1.2, 'line-color': '#9a9a90', opacity: 0.55, 'curve-style': 'bezier', 'target-arrow-shape': 'triangle', 'target-arrow-color': '#9a9a90', 'arrow-scale': 0.6 } },
      { selector: 'edge[kind = "conflict"]', style: { width: 2.5, 'line-color': conflict, 'line-style': 'dashed', opacity: 1, 'target-arrow-shape': 'none' } },
      { selector: ':selected', style: { 'overlay-opacity': 0.12, 'overlay-color': conflict } },
    ],
    layout: { name: 'cose', animate: false, padding: 24, nodeRepulsion: 9000, idealEdgeLength: 70, randomize: false },
  });

  const caption = $('#caption');
  state.cy.on('tap', 'node', (evt) => {
    const d = evt.target.data();
    const degree = d.degree == null ? '' : ` · ${d.degree} edges in the Spain slice`;
    caption.innerHTML = `<b>${esc(d.full)}</b> · ${esc(d.type.replace('_', ' '))}${esc(degree)} · <code>${esc(d.id)}</code>`;
  });
  state.cy.on('tap', 'edge', (evt) => {
    const d = evt.target.data();
    const from = state.cy.getElementById(d.source).data('full');
    const to = state.cy.getElementById(d.target).data('full');
    caption.innerHTML = `<b>${esc(from)}</b> ${esc(PREDICATE_TEXT[d.predicate] || d.predicate)} <b>${esc(to)}</b> · source <code>${esc(d.provenance)}</code> (${esc(d.source_class)}) · confidence ${Number(d.confidence).toFixed(2)}`;
  });
}

// export ------------------------------------------------------------------

function exportCsv() {
  const header = ['cluster_id', 'event_date', 'artists', 'event_ids', 'reading', 'confidence', 'verdict', 'by', 'note', 'updated_at'];
  const rows = [];
  for (const [clusterId, docs] of state.verdicts) {
    const cluster = state.byId.get(clusterId);
    for (const d of docs) {
      rows.push([
        clusterId, cluster?.event_date ?? '', cluster?.artists.join('; ') ?? '',
        (d.eventIds || cluster?.events.map((e) => e.node_id) || []).join('; '),
        d.reading, d.confidence, d.verdict, d.byName, d.note, d.updatedAt,
      ]);
    }
  }
  if (rows.length === 0) { toast('No verdicts to export yet.'); return; }
  const cell = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const csv = [header, ...rows].map((r) => r.map(cell).join(',')).join('\n');
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  link.download = `ekg-conflict-verdicts-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

function renderFooter() {
  const s = state.snapshot;
  const exported = new Date(s.exported_at).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
  $('#foot').innerHTML = `
    <span>Snapshot ${esc(exported)} · ${esc(s.slice)} · commit ${esc(s.git_commit)}</span>
    <span><kbd>j</kbd>/<kbd>k</kbd> move · <kbd>1</kbd>–<kbd>4</kbd> verdict · <kbd>/</kbd> search</span>
    <button id="rules">How conflicts are found</button>
    <button id="export">Export verdicts (CSV)</button>`;
  $('#rules').addEventListener('click', () => alert(s.rules.join('\n\n')));
  $('#export').addEventListener('click', exportCsv);
}

// wiring ------------------------------------------------------------------

function move(step) {
  const clusters = visibleClusters();
  if (clusters.length === 0) return;
  const index = clusters.findIndex((c) => c.id === state.selectedId);
  const next = clusters[Math.min(clusters.length - 1, Math.max(0, index + step))];
  select(next.id);
}

function wire() {
  $('#filters').addEventListener('click', (evt) => {
    const chip = evt.target.closest('[data-filter]');
    if (!chip) return;
    state.filter = chip.dataset.filter;
    renderList();
  });
  $('#search').addEventListener('input', (evt) => {
    state.query = evt.target.value.trim().toLowerCase();
    renderList();
  });
  $('#clusters').addEventListener('click', (evt) => {
    const card = evt.target.closest('[data-id]');
    if (card) select(card.dataset.id);
  });
  $('#detail').addEventListener('click', (evt) => {
    const choice = evt.target.closest('[data-verdict]');
    if (choice) recordVerdict(choice.dataset.verdict);
  });
  document.addEventListener('keydown', (evt) => {
    if (document.body.dataset.view !== 'conflicts') return;
    const typing = evt.target.matches('input, textarea');
    if (evt.key === 'Escape' && typing) { evt.target.blur(); return; }
    if (typing || evt.metaKey || evt.ctrlKey || evt.altKey) return;
    if (evt.key === 'j' || evt.key === 'ArrowDown') { evt.preventDefault(); move(1); }
    else if (evt.key === 'k' || evt.key === 'ArrowUp') { evt.preventDefault(); move(-1); }
    else if (evt.key === '/') { evt.preventDefault(); $('#search').focus(); }
    else {
      const verdict = VERDICTS.find((v) => v.shortcut === evt.key);
      if (verdict) recordVerdict(verdict.key);
    }
  });
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    const cluster = state.byId.get(state.selectedId);
    if (cluster) renderDetail(cluster);
  });
}

async function connectBackend() {
  if (!window.disco) {
    toast('Disco backend unavailable: verdicts are read-only here.');
    return;
  }
  try {
    state.me = await disco.id();
    if (state.me?.name) disco.id.setName(state.me.name);
    state.collection = disco.db.collection('verdicts');
    await loadVerdicts();
    state.collection.subscribe({
      onCreate: (doc) => { upsertVerdictDoc(doc); refreshAfterRemoteChange(doc.clusterId); },
      onUpdate: (doc) => { upsertVerdictDoc(doc); refreshAfterRemoteChange(doc.clusterId); },
      onDelete: (id) => { removeVerdictDoc(id); refreshAfterRemoteChange(null); },
    });
    joinRoom();
    renderPeople();
  } catch (error) {
    console.error('disco backend failed', error);
    toast('Could not reach the Disco backend; verdicts are unavailable.');
  }
}

function refreshAfterRemoteChange(clusterId) {
  renderList();
  renderStats();
  const cluster = state.byId.get(state.selectedId);
  if (cluster && (clusterId === null || clusterId === cluster.id)) renderVerdict(cluster);
}

async function main() {
  try {
    const response = await fetch('data/snapshot.json');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.snapshot = await response.json();
  } catch (error) {
    $('#detail').innerHTML = `<div class="empty">Could not load data/snapshot.json (${esc(error.message)}). Run <code>uv run python disco/export_snapshot.py --load slices/es.pkl</code> and redeploy.</div>`;
    return;
  }
  state.byId = new Map(state.snapshot.clusters.map((c) => [c.id, c]));
  wire();
  renderStats();
  renderFooter();
  renderList();

  let remembered = null;
  try { remembered = localStorage.getItem('ekg-conflicts:selected'); } catch { /* storage may be blocked */ }
  select(state.byId.has(remembered) ? remembered : state.snapshot.clusters[0]?.id);

  await connectBackend();
  renderList();
  renderStats();
  const cluster = state.byId.get(state.selectedId);
  if (cluster) renderVerdict(cluster);
}

main();
