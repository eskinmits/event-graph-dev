# event-graph

An **Event Knowledge Graph** over TicketSwap events and their metadata — artists, venues,
genres, organizers, ticket providers, Event Engine imports — modelled as explicit nodes and
typed, provenance-carrying edges, so that the *structure* of the graph can be used to infer
the links we are missing.

Today those relationships are computed at query time with a join, which means we can only
ask questions whose shape we already know. Stored edges let many individually-weak signals
combine into one confident answer — and, crucially, into one that can be **explained**.

The deliverable is a ranked, explained worklist of proposed links ops can action. Not a
graph that merely exists.

## How it fits together

```
ClickHouse (canonical)            Python (disposable)              You
─────────────────────             ───────────────────              ───
dbt_dev.dim_graph_nodes    ──▶  EventGraph (rustworkx)     ──▶   graph serve  (browser)
dbt_dev.bridge_graph_edges_source    │                           graph show   (terminal)
                                       │
dbt_dev.graph_edges_inferred  ◀──────┘  inference writes back
```

ClickHouse is the source of truth. The in-memory projection holds no unique information —
delete it, rebuild it, and you are exactly where you were. Inference results round-trip back
through ClickHouse rather than being written where they are computed.

Two things follow from that, and they are not negotiable:

- **Inference reads `bridge_graph_edges_source`, never the union view.** A run's own guess
  must never become the next run's evidence.
- **Every edge carries `source`, `source_class` and `confidence`.** An inferred edge and a
  human-verified one are separate rows that coexist — never a field one overwrites. Rollback
  is a delete by `run_id`.

## Setup

ClickHouse is only reachable through the aws-service-access bastion tunnel. Start it first
and leave it running:

```bash
cd ~/Dev/aws-service-access && env AWS_SSO_GROUP=Machine-Learning bash tunnel.sh \
  clickhouse_transformation_http clickhouse_transformation_native \
  clickhouse_ingest_http clickhouse_ingest_native
```

One-time per machine, the VPC endpoint hostnames must resolve to loopback or TLS fails on
the name — see `.env.example` for the `/etc/hosts` lines and the port map.

Then:

```bash
uv sync
cp .env.example .env        # fill in CLICKHOUSE_USER / CLICKHOUSE_PASSWORD
uv run event-graph ping     # prints the server version if the tunnel is up
```

## Quickstart

The full graph is 7.7M nodes and 19.3M edges. It fits in memory but is slow to iterate on,
so work against a **market slice** — a country's events expanded two hops outwards.

Build one and cache it, then explore it:

```bash
uv run event-graph graph build --country ES --save slices/es.pkl   # ~3.5 min, once
uv run event-graph graph serve --load slices/es.pkl                # ~20s to load
```

Open <http://127.0.0.1:8000>, type an event name, and click a result.

`--load` is the difference between exploring the graph and waiting for it: the slice loads
in seconds instead of minutes, and once the process is up a search over ~1M nodes takes
~0.3s and a two-hop neighbourhood ~0.05s. Slices live in `slices/`, which is gitignored —
they are a cache, rebuildable from ClickHouse at any time.

## Commands

| Command | What it does |
|---|---|
| `event-graph ping` | Check the ClickHouse connection |
| `event-graph graph build` | Build a projection and report what it contains; `--save` caches it |
| `event-graph graph serve` | Browser explorer over one projection |
| `event-graph graph show <node_id>` | Same walk, printed to the terminal |
| `event-graph graph ontology` | Print the node and edge vocabulary this repo understands |

Add `-v` before the subcommand for progress logging: `event-graph -v graph build …`.

### Slice flags

Shared by every command that builds a projection:

| Flag | Effect |
|---|---|
| `--country NL` | Slice to one market. Seeds on **every** event in it, past included (D11) |
| `--predicate performs_at` | Restrict to these predicates. Repeatable |
| `--min-confidence 0.7` | Drop weaker edges |
| `--max-edges N` | Cap edges loaded — useful for a fast smoke test |
| `--keep-junk-hubs` | Keep "Unbekannt" and friends. They fuse unrelated clusters; off by default |
| `--prune-above-degree N` | Drop nodes above a degree ceiling once the graph is built |
| `--lenient` | Count edges that break the ontology instead of failing the build |

`--load PATH` replaces all of these — it reads a slice that was already built, so the flags
that shaped it no longer apply.

## The explorer

`graph serve` holds one projection in memory for the life of the process and serves a search
box over it. Everything after the first load is instant.

What you are looking at:

- **Colour** carries the three node types that dominate a neighbourhood — event, artist,
  venue. Every other type is identified by its **shape and label**, so no hue is spent on a
  fourth and the palette stays colourblind-safe.
- **Edge thickness** is confidence. **Dashed** means inferred rather than sourced. **Dark**
  means the edge is on the path that explains why a node is in the picture at all.
- **Rings** are hops from the node you searched for.
- Hovering an edge gives `predicate · source · confidence`; clicking a node opens its full
  explanation and an edge table.

The view is capped at 300 nodes and says so when it truncates — past that a neighbourhood is
a hairball nobody can read.

Note the walk stops at **classification** nodes (genre, city, ticket_provider,
organizer_company) by default. They are hubs by construction — 55 genre nodes carry 3.2M
`has_genre` edges — so expanding through one reaches most of the graph from anywhere. They
are still loaded and still visible as leaves; the walk just does not continue out of them.
`--through-classifications` in the UI overrides this when you actually want that hop.

## Development

```bash
uv run pytest        # the suite runs with no tunnel and no credentials
uv run mypy          # strict
```

Tests drive the same code path as production through `InMemoryGraphSource`, so nothing in
the suite needs a database. Keep it that way: nothing expensive may happen at import time —
`--help` and test collection must work on a laptop with no tunnel.

Layout worth knowing:

- `graph/ontology.py` is the authority on what the graph may contain. A value ClickHouse
  holds that this module does not know is a mismatch to reconcile, not something to pass
  through silently.
- `graph/source.py` is the seam between ClickHouse and the projection — including the CTE
  chain that walks a market slice.
- `web/` is the explorer: a stdlib HTTP server and a vendored Cytoscape.js, no new
  dependencies and no build step.

## Known data traps

These have all been measured. Ignoring one produces a confident wrong answer, not an obvious
failure:

- **Junk hub nodes.** "Unbekannt" (German for *unknown*) carries 9.6k edges across two
  artist rows; also "Auditorium", "Saturday Night". Leave them in and they fuse unrelated
  clusters. Filtered by default.
- **Artist genre tags are wrong in places.** André Rieu tagged *Rock*, Olivia Dean tagged
  *Rap*. `artist_tags` is a real edge, but propagating from it uncritically spreads errors.
- **Genre is heavily skewed.** The top five genres are 70% of tagged events, so "always
  guess Pop" looks deceptively strong. Evals therefore report **lift over a baseline, never
  raw accuracy**.
- **Tribute and cover shows match the original artist.** Title matching reaches 66.5% of
  artist-less events, but the most-matched names are Skyline, Hans Zimmer, Coldplay, ABBA,
  Queen — Candlelight and tribute acts, not the artists. Wrong artist links are user-visible.
- **Scheduling conflicts are not always errors.** An artist plays a festival and their own
  set on the same date; artists link to a festival as a whole, not to their day.
- **Redirects chain.** 28k `same_as` edges point at a target that is itself redirected, so
  resolving them needs transitive closure rather than one hop.

## Where the thinking lives

This README and `CLAUDE.md` both drift. Notion is the source of truth:

- [EKG Event Knowledge Graph](https://app.notion.com/p/3e29b6c71158815b8f2ed4eb4305f003) — problem, scope, success criteria
- [Decision Log](https://app.notion.com/p/3e29b6c711588146aae9d0b470ce8a1a) — D1–D11, with the reasoning. A reopened decision gets a **new** numbered entry, never an edit to an old one
- [Ontology & Edge Model](https://app.notion.com/p/3e29b6c711588142b75aec14a8a6afc1) — node and edge types
- [Progress & Updates](https://app.notion.com/p/3e39b6c7115881deb5c3fd79a08d9896) — running log across the dbt side and the graph side
