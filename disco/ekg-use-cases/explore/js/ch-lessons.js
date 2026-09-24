/* chapter 8: the scoreboard, the honest negatives, the shape of the system and what comes next */
(function () {
  const { $ } = EKG;

  const CARDS = [
    ["win", "1,413", "explained artist links, written to ClickHouse", "Each has its reasons and a confidence equal to the rule's measured precision: 90% NL, 94% ES. Nothing below 80% reaches ops."],
    ["win", "4 of 8", "confirmed by a human, independently", "An employee hand-linked “Audio Obscura x EXHALE by Amelie Lens” two hours after our run. Four of their eight links (Amelie Lens, HAAi, DAX J, Anfisa Letyago) were already our proposals."],
    ["win", "38.6%", "of ops' merges are invisible to title matching", "55,142 of 142,781 merged pairs had different titles. A shared artist is the strongest duplicate signal (3.49×) and beats an identical title."],
    ["win", "2,968", "duplicate artist rows found", "In 2,533 clusters. 100 of 100 reviewed merges were the same act. Duplicates are what caps artist ranking today."],
    ["win", "7%", "of new links uncovered a duplicate event", "Defected Closing = Dennis Ferrer at club chinois; DC-10 = DC10; Razzmatazz 2 = Razzmatazz. Linking finds duplicates, and deduplication makes linking better."],
    ["win", "993", "junk nodes flagged by rules", "Unbekannt, TBA, Halloween, venues that are really a city name. Flagging 41 junk artists removed 68.5% of the fake scheduling conflicts."],
    ["neg", "5", "upcoming events gain an artist from merged duplicates", "The ‘cheapest win’ was a footnote: merges clean up history, while the gap is in upcoming events. It works for genre instead (3,177 live events)."],
    ["neg", "≈ baseline", "structure alone predicts artists about as well as venue history", "The early 2× lift came from duplicate listings leaking the answer. We caught it and reported it. The walk earns its keep as a shortlist."],
    ["neg", "+6 / −2", "points for graph ranking vs popularity (NL / ES)", "Usually the “wrong” candidate is a duplicate of the right one. Deduplicating artists has to come first."],
    ["neg", "~200", "ops-reviewed pairs: the cheapest unblock on the board", "The merge log measures recall, not precision. A small reviewed sample turns lower bounds into numbers we can automate on."],
    ["dec", "Lens", "ClickHouse is the source of truth. The graph is a lens, not a ledger.", "Any graph engine is a disposable copy. Kùzu got archived by its owner mid-project, and switching away cost us nothing."],
    ["dec", "Provenance", "Every edge says who claimed it and how sure we are", "Inferred and verified facts are separate rows. Undoing a whole batch of proposals is one step."],
    ["dec", "Baselines", "Lift over a baseline, never raw accuracy", "“Always guess Pop” looks strong when the top five genres are 70% of events. Every number here is compared against the dumb answer."],
    ["dec", "Guardrails", "Walks never pass through genres or cities", "55 genre nodes carry 3.2M edges. Walking through them reaches everything and means nothing. One rule took Iceland's slice from 1.2M nodes to 13k."],
  ];

  const ROADMAP = [
    ["Build it nightly", "The graph tables refresh every night in production."],
    ["Nightly inference", "New proposals every night, each batch kept separate and reversible."],
    ["Learn from ops", "Every review decision becomes a label and a trusted fact."],
    ["Junk rules every night", "The next Unbekannt is caught automatically."],
    ["Check drafts before go-live", "97.7% of merges happen before the event. Catch them at creation."],
    ["Write fixes back", "Above one threshold suggest to ops, above another apply automatically."],
  ];

  const ARCH = `<svg class="arch" viewBox="0 0 1100 250" role="img" aria-label="Architecture: ClickHouse tables feed an in-memory graph whose inferences go back to ClickHouse and on to ops">
    <defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#7f7d76"/></marker></defs>
    <g font-family="Inter, sans-serif" font-size="13" fill="#b9b7ae">
      <rect x="10" y="40" width="250" height="170" rx="14" fill="#171a22" stroke="#2a2f3b"/>
      <text x="30" y="70" fill="#00b6f0" font-weight="700" font-size="12" letter-spacing="1.2">CLICKHOUSE · CANONICAL</text>
      <text x="30" y="100" fill="#f3f2ee" font-weight="600">nodes</text><text x="30" y="118">7.7M, 10 types</text>
      <text x="30" y="146" fill="#f3f2ee" font-weight="600">edges</text><text x="30" y="164">19.3M, 11 relationship types</text>
      <text x="30" y="192">+ 109k derived scheduling clashes</text>
      <rect x="330" y="40" width="220" height="170" rx="14" fill="#171a22" stroke="#2a2f3b"/>
      <text x="350" y="70" fill="#9085e9" font-weight="700" font-size="12" letter-spacing="1.2">PYTHON · DISPOSABLE</text>
      <text x="350" y="100" fill="#f3f2ee" font-weight="600">in-memory graph, per market</text><text x="350" y="118">NL: 1.66M nodes, loads in 36s</text>
      <text x="350" y="146" fill="#f3f2ee" font-weight="600">lineup linking, PageRank,</text><text x="350" y="164" fill="#f3f2ee" font-weight="600">dedup, junk rules</text>
      <text x="350" y="192">every proposal carries evidence</text>
      <rect x="620" y="40" width="220" height="170" rx="14" fill="#171a22" stroke="#2a2f3b"/>
      <text x="640" y="70" fill="#ffd166" font-weight="700" font-size="12" letter-spacing="1.2">WRITE-BACK</text>
      <text x="640" y="100" fill="#f3f2ee" font-weight="600">inferred links</text><text x="640" y="118">stored apart from facts</text>
      <text x="640" y="146" fill="#f3f2ee" font-weight="600">one combined view</text><text x="640" y="164">facts + derived + inferred</text>
      <text x="640" y="192">any batch undone in one step</text>
      <rect x="910" y="40" width="180" height="170" rx="14" fill="#171a22" stroke="#2a2f3b"/>
      <text x="930" y="70" fill="#2fbf71" font-weight="700" font-size="12" letter-spacing="1.2">PEOPLE</text>
      <text x="930" y="100" fill="#f3f2ee" font-weight="600">ops worklists</text><text x="930" y="118">merges, links, review</text>
      <text x="930" y="146" fill="#f3f2ee" font-weight="600">ML dedup model</text><text x="930" y="164">reads graph features</text>
      <text x="930" y="192">verdicts become labels</text>
      <line x1="262" y1="125" x2="326" y2="125" stroke="#7f7d76" stroke-width="2" marker-end="url(#ah)"/>
      <line x1="552" y1="125" x2="616" y2="125" stroke="#7f7d76" stroke-width="2" marker-end="url(#ah)"/>
      <line x1="842" y1="125" x2="906" y2="125" stroke="#7f7d76" stroke-width="2" marker-end="url(#ah)"/>
      <path d="M1000 212 C1000 245, 135 245, 135 214" fill="none" stroke="#3d4453" stroke-width="2" stroke-dasharray="5 5" marker-end="url(#ah)"/>
      <text x="470" y="240" fill="#7f7d76" font-size="12">inference never reads its own output: it reads source edges only</text>
    </g></svg>`;

  const chapter = {
    id: "lessons",
    title: "What we learned",
    init(stage) {
      const tag = { win: ["win", "Worked"], neg: ["neg", "Honest negative"], dec: ["dec", "Principle"] };
      this.el = EKG.h(`<section class="chapter" id="ch-lessons"><div class="lessons"><div class="lessons-inner">
        <div>
          <div class="eyebrow">What we learned in three days</div>
          <h2 style="font-size:38px;margin-top:6px;max-width:900px">A graph is a shared place where every system's claims about an event sit side by side, <span style="color:var(--accent)">so they can check each other.</span></h2>
          <p class="lede" style="margin-top:12px;max-width:820px">Deduplication, artist linking, prediction and recommendations aren't four projects. They're four questions asked of one set of tables, and each answer improves the others.</p>
        </div>
        <div class="grid-cards">${CARDS.map(([k, num, head, body]) => `<div class="card lesson"><span class="tag ${tag[k][0]}">${tag[k][1]}</span><div class="num">${num}</div><h3 style="font-size:15px;margin-bottom:6px">${head}</h3><p>${body}</p></div>`).join("")}</div>
        <div class="card"><h3>How it fits together</h3>${ARCH}</div>
        <div><h3 style="margin-bottom:10px">Making it permanent</h3><div class="roadmap">${ROADMAP.map(([h, b], i) => `<div class="card"><div class="n">${i + 1}</div><h3 style="font-size:14.5px;margin:4px 0">${h}</h3><p class="muted">${b}</p></div>`).join("")}</div></div>
        <p class="muted">Every number in this app is measured, and can be regenerated from TicketSwap data with one command.</p>
      </div></div></section>`);
      stage.appendChild(this.el);
    },
    async enter() {},
  };

  EKG.register(chapter);
})();
