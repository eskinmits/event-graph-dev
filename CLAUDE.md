# event-graph

## What this is

A POC for the **[EKG] Event Knowledge Graph** (Disco Days 2026 Q3 hackathon, 3 days,
demo Thursday). It models TicketSwap events and their metadata — artists, venues, genres,
organizers, ticket providers, Event Engine imports — as explicit nodes and typed,
provenance-carrying edges, and uses the *structure* of that graph to infer links we are missing.

Today those relationships are computed at query time with a join, so we can only ask questions
whose shape we already know. Stored edges let us combine many individually-weak signals into one
confident answer.

**The gap it targets** (`dbt.dim_events`, 290k upcoming events): 72% have no artist linked,
42% no genre, 89.6% no ticket provider. Plus 37.7k surplus artist rows and 29.4k surplus venue
rows that are exact case-folded name collisions.

**Deliverable:** a ranked, explained worklist of proposed links ops can action — not a graph that
merely exists. Demo is "type an event, see its neighbourhood, see what we inferred and *why*".

## Decisions that constrain the code

These are settled (Notion Decision Log D1–D9). Do not silently re-litigate them; if one is
reopened, it gets a new numbered entry in Notion.

- **ClickHouse is canonical** (D1). Node and edge tables built by dbt are the source of truth.
  Any graph engine is a disposable projection rebuilt from them, holding no unique information.
  Inference results round-trip back through ClickHouse. The graph tables live in the `dbt_dev`
  schema (`dim_graph_nodes`, `bridge_graph_edges_source`, `graph_edges_inferred`); the source
  dims such as `dim_events` stay in `dbt`. Defaults are in `GraphTablesConfig` in `config.py`.
- **No graph database in the dependency path** (D2, D3). Compute with `rustworkx` in Python.
  The inference work is mostly bounded-hop, which is joins, and ClickHouse is good at those.
  Kùzu was evaluated and rejected — the repo was archived by its owner in Oct 2025.
- **Scope is artist linking + dedup together** (D4). Recommendations are out.
- **Every edge carries `source` and `confidence`** (D6). Inferred and human-verified edges are
  separate rows, never a field one overwrites. Rollback is a delete by `run_id`.
- **Evals report lift over a baseline, never raw accuracy** (D7). Genre against majority-class,
  artist ranking against most-popular-candidate.
- **This is a substrate for `ml-event-deduplication`, not a competitor** (D8). We produce features
  that model consumes; we do not build a competing scorer.

## Known data traps

- **Junk hub nodes.** "Unbekannt" (German for *unknown*) has 1,868 conflicting events; also
  "Auditorium", "Saturday Night". Leave them in and they fuse unrelated clusters. Filter before
  clustering.
- **Artist genre tags are wrong in places.** André Rieu tagged *Rock*, Olivia Dean tagged *Rap*.
  `artist_tags` is a real edge, but propagating from it uncritically spreads errors.
- **Genre is heavily skewed.** Top five genres are 70% of tagged events. "Always guess Pop" looks
  deceptively strong — hence D7.
- **Scheduling conflicts are not always errors.** An artist plays a festival and their own set on
  the same date. Artists link to a festival as a whole, not to their day.
- **Tribute and cover shows match the original artist.** Title matching reaches 66.5% of artist-less
  events, but the most-matched names are Skyline, Hans Zimmer, Coldplay, ABBA, Queen — Candlelight
  and tribute acts. Wrong artist links are user-visible. Needs a tribute detector plus a blocklist
  for artist names that are common words.

## Setup

ClickHouse is only reachable through the aws-service-access bastion tunnel — start it before
running anything that queries. See `.env.example` for the command and the port map.

```
uv sync
uv run event-graph ping   # verifies the connection
```

## Notion

CLAUDE.md summarises these and will drift — they are the source of truth.

- [EKG Event Knowledge Graph](https://app.notion.com/p/3e29b6c71158815b8f2ed4eb4305f003) — the parent page: problem, scope, success criteria
- [Decision Log](https://app.notion.com/p/3e29b6c711588146aae9d0b470ce8a1a) — D1–D9 in full, with the reasoning. New decisions get a new numbered entry here, never an edit to an old one
- [Ontology & Edge Model](https://app.notion.com/p/3e29b6c711588142b75aec14a8a6afc1) — node and edge types
- [Hypothesis](https://app.notion.com/p/3e29b6c71158815195d5d4482a233157)
- [Source Confidence Audit](https://app.notion.com/p/3e39b6c7115881ed8a11e5456739af6e)
- [Progress & Updates](https://app.notion.com/p/3e39b6c7115881deb5c3fd79a08d9896) — the running log of changes across the dbt side and the graph side. Keep it current unprompted: every change the other side could trip over (a renamed column, a new edge type, a rebuilt table, a measurement that invalidates an assumption) gets a dated, `dbt`/`graph`/`both`-tagged entry, and anything needing the other side goes in the Open handoffs table at the top. Decisions still go to the Decision Log; this page links to them

## Coding principles

- **Typed.** Annotate every function signature and every attribute. Prefer `X | None` over
  `Optional[X]`, built-in generics over `typing.List`. Type-check before claiming something works.
- **Object-oriented where state or behaviour clusters** — nodes, edges, the graph, the loaders,
  the rankers, the eval harnesses. Use classes to make the ontology explicit rather than passing
  around bare dicts and tuples. Pydantic models for anything crossing a boundary (config, rows
  read from ClickHouse, edges written back).
- **Functional where it is simply a transformation.** Do not wrap a pure function in a class for
  the sake of it, and do not build an inheritance hierarchy where a function taking a parameter
  would do. Be practical.
- **No comments unless genuinely necessary** — only for a non-obvious *why*, never restating
  *what* the line does. Add a docstring where the purpose is not evident from the name and
  signature; skip it where it is.
- **Comments and log messages are always lowercase.** `# tribute acts poison the candidate set`,
  `logger.info("loaded %d edges", n)` — not sentence case, not title case. Applies to inline
  comments, `TODO`s and every log line. Docstrings are prose and keep normal capitalisation, as do
  exception messages and CLI output the user reads.
- **Fail with a readable error.** Missing config, an absent tunnel, an empty result set: say what
  is wrong and what to do, do not let a library traceback be the whole message.
- **Nothing expensive at import time.** No connections, no queries, no file reads when a module is
  imported — `--help` and test collection must work with no tunnel and no credentials.
- **Every number is reproducible.** An eval result that cannot be regenerated from a command is
  not a result.

## Conventions

- `uv` for dependencies and running; never `pip` directly.
- Configuration goes in `config.py` as pydantic-settings, read from the environment or `.env`.
  Never `os.environ` at a call site.
- Secrets live in `.env` (gitignored). `.env.example` documents the keys with empty values.
