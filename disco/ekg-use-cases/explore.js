/* use cases 3 and 4 are chapters of the event knowledge graph explorer (explore/), shown in one frame. It keeps the data
   (hundreds of shards per market), so it loads once, only when one of these tabs is first opened,
   and switching between them only changes the fragment, which does not reload the frame. */
(function () {
  // the explorer lives in explore/ inside this site, so it shares this app's Disco backend and login
  const BASE = 'explore/';
  let frame = null;

  function show(chapter) {
    // the query stays fixed: a changed query would reload the frame, a changed fragment does not
    const url = `${BASE}?embed=1#${encodeURIComponent(chapter)}`;
    if (!frame) {
      frame = document.createElement('iframe');
      frame.title = 'Event knowledge graph chapter';
      frame.src = url;
      EKG.$('#explore-frame').replaceChildren(frame);
    } else if (!frame.src.endsWith(`#${chapter}`)) {
      frame.src = url;
    }
    const open = EKG.$('#explore-open');
    open.href = `${BASE}#${encodeURIComponent(chapter)}`;
  }

  window.UseCases = window.UseCases || {};
  window.UseCases.explore = { show };
})();
