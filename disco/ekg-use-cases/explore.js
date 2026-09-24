/* use cases 3 and 4 are chapters of ekg-explore, shown in one frame. ekg-explore keeps the data
   (hundreds of shards per market), so it loads once, only when one of these tabs is first opened,
   and switching between them only changes the fragment, which does not reload the frame. */
(function () {
  // deployed, both apps sit side by side on the same host; in `disco preview` ekg-explore runs on its own port
  const BASE = location.hostname === 'localhost' ? 'http://localhost:4401/' : '../ekg-explore/';
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
