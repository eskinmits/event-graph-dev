/* boot: chapters, navigation, market switch and who else is watching */
(function () {
  const { $, $$, esc, state } = EKG;
  // ?embed=<chapter>: shown inside another app's tab, so one chapter, no chapter bar, no paging
  const embed = new URLSearchParams(location.search).get("embed");
  if (embed) document.body.classList.add("embed");

  function joinRoom() {
    if (typeof window.disco === "undefined") return;
    try {
      const room = disco.channel("ekg-demo-stage");
      room.on("presence", ({ members }) => {
        const n = (members || []).length;
        $("#presence").innerHTML = n > 1 ? `<b>●</b> ${n} watching` : "";
      });
    } catch (err) {
      console.warn("channel unavailable", err);
    }
  }

  async function start() {
    const stage = $("#stage");
    state.chapters.forEach((c) => c.init(stage));
    $("#chapters").innerHTML = state.chapters.map((c, i) => `<button data-go="${c.id}"><span class="n">${i + 1}</span>${esc(c.title)}</button>`).join("");
    $$("[data-go]").forEach((b) => (b.onclick = () => EKG.go(b.dataset.go)));
    $$(".market button").forEach((b) => (b.onclick = () => EKG.setMarket(b.dataset.market)));

    try {
      await EKG.loadMarket(state.market);
    } catch (err) {
      $("#loading").innerHTML = `<p>Could not load the graph data: ${esc(err.message)}</p>`;
      return;
    }
    $("#loading").remove();
    joinRoom();
    EKG.loadIndex().catch(() => {});

    document.addEventListener("keydown", (e) => {
      if (embed || e.target.closest("input, textarea")) return;
      if (e.key === "ArrowRight") EKG.step(1);
      if (e.key === "ArrowLeft") EKG.step(-1);
    });
    setTimeout(() => $("#keysHint").classList.add("gone"), 6000);
    window.addEventListener("resize", () => state.current && state.current.view && state.current.view.resize());

    const wanted = location.hash.slice(1) || embed || "";
    await EKG.go(state.chapters.some((c) => c.id === wanted) ? wanted : "intro", { push: false });
    // the host app switches chapters by changing only the fragment, which does not reload
    if (embed) {
      window.addEventListener("hashchange", () => {
        const id = location.hash.slice(1);
        if (state.chapters.some((c) => c.id === id)) EKG.go(id, { push: false });
      });
    }
    // warm the other market in the background so the switch is instant on stage
    setTimeout(() => EKG.loadMarket(state.market === "NL" ? "ES" : "NL").then(() => EKG.loadIndex(state.market === "NL" ? "ES" : "NL")).catch(() => {}), 1500);
  }

  start();
})();
