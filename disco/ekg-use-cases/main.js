/* boot: the four use-case tabs, keyboard navigation, and who else is watching */
(function () {
  const { $, $$, esc } = EKG;
  const TABS = ['conflicts', 'artists', 'linking', 'recommend'];
  const EMBEDDED = new Set(['linking', 'recommend']);
  const chapters = window.UseCases;
  let current = null;

  async function show(tab) {
    if (!TABS.includes(tab)) tab = TABS[0];
    if (tab === current) return;
    current = tab;
    document.body.dataset.tab = tab;
    $$('#chapters button').forEach((b) => b.classList.toggle('on', b.dataset.tab === tab));
    const section = EMBEDDED.has(tab) ? 'ch-explore' : `ch-${tab}`;
    $$('.chapter').forEach((el) => el.classList.toggle('on', el.id === section));
    history.replaceState(null, '', tab === TABS[0] ? location.pathname + location.search : `#${tab}`);
    try { localStorage.setItem('ekg-use-cases:tab', tab); } catch { /* storage may be blocked */ }
    if (EMBEDDED.has(tab)) {
      chapters.explore.show(tab);
      return;
    }
    const loading = $('#loading');
    const slow = setTimeout(() => loading.classList.add('on'), 150);
    try {
      await chapters[tab].enter();
    } catch (err) {
      $(`#ch-${tab} .story`).insertAdjacentHTML('beforeend', `<div class="callout warn">Could not load this use case: ${esc(err.message)}</div>`);
    } finally {
      clearTimeout(slow);
      loading.classList.remove('on');
    }
  }

  function joinRoom() {
    if (typeof window.disco === 'undefined') return;
    try {
      const room = disco.channel('ekg-use-cases');
      room.on('presence', ({ members }) => {
        const n = (members || []).length;
        $('#presence').innerHTML = n > 1 ? `<b>●</b> ${n} watching` : '';
      });
    } catch (err) {
      console.warn('channel unavailable', err);
    }
  }

  function start() {
    chapters.conflicts.init();
    chapters.artists.init();
    $$('#chapters button').forEach((b) => (b.onclick = () => show(b.dataset.tab)));
    document.addEventListener('keydown', (e) => {
      if (e.target.closest('input, textarea') || e.metaKey || e.ctrlKey || e.altKey) return;
      const i = TABS.indexOf(current);
      if (e.key === 'ArrowRight') show(TABS[Math.min(TABS.length - 1, i + 1)]);
      else if (e.key === 'ArrowLeft') show(TABS[Math.max(0, i - 1)]);
      else if (!EMBEDDED.has(current) && (e.key === 'j' || e.key === 'ArrowDown')) { e.preventDefault(); chapters[current].move(1); }
      else if (!EMBEDDED.has(current) && (e.key === 'k' || e.key === 'ArrowUp')) { e.preventDefault(); chapters[current].move(-1); }
      else if (e.key === '/' && !EMBEDDED.has(current)) { e.preventDefault(); $(`#ch-${current} .search`).focus(); }
    });
    window.addEventListener('resize', () => {
      if (!EMBEDDED.has(current)) chapters[current]?.view?.resize();
    });
    joinRoom();
    let remembered = null;
    try { remembered = localStorage.getItem('ekg-use-cases:tab'); } catch { /* storage may be blocked */ }
    const wanted = location.hash.slice(1);
    show(TABS.includes(wanted) ? wanted : TABS.includes(remembered) ? remembered : TABS[0]);
  }

  start();
})();
