The event knowledge graph explorer (Mustafa Eskin's `event-knowledge-graph` Disco app), copied
2026-09-24 so ekg-use-cases can show its *Missing artists* and *Recommend* chapters from the same
site. It replaced the separate `ekg-explore` site, which now forwards here.

Two changes from the original:

- **Embed mode** (`js/main.js`, `css/app.css`): `?embed=1` hides the brand, chapter bar, key hint
  and presence, turns off arrow-key paging, takes the chapter from the fragment and follows
  `hashchange`. Without `?embed`, it behaves exactly as the original.
- **SDK path** (`index.html`): `../__disco.js`, because Disco serves the SDK at the site root only.

`data/` (~190 MB of NL/ES bundles and shards) is not in git. To refresh it, pull the original and
copy its data across:

    disco pull event-knowledge-graph /tmp/ekg && rsync -a /tmp/ekg/data/ disco/ekg-use-cases/explore/data/
