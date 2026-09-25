# bin

Measurement scripts. Every number quoted in Notion should be regenerable by running one of
these, with no arguments, against a live tunnel.

The redirect scripts read `dbt.stg_backend__event_redirected` rather than
`dbt_dev.bridge_graph_edges_source`, so they keep working while the graph tables are
unavailable — the staging table is what dbt derives the `event_redirect` edges from, so the
closure is the same one `EventGraph.components({Predicate.SAME_AS})` produces.

| Script | Answers |
|---|---|
| `redirect_transfer_coverage.py` | How many events can have artist, genre or ticket provider filled in from a redirect sibling, across four populations |
| `redirect_duplicate_precision.py` | Is a redirect really a "same event" assertion? This is what licenses using the redirect log as labelled ground truth |
| `duplicate_name_reach.py` | How much of the upcoming catalogue sits behind a *live* duplicated artist or venue, plus the common-word artist blocklist |
| `junk_nodes.py` | Which nodes are junk hubs (placeholder artists, theme nights, city-named venues, deletion sinks) and which are real nodes carrying wrong links. Writes a seed-shaped CSV |
| `duplicate_detection_backtest.py` | Could we surface a duplicate event before ops manually redirects it? Rule recall, candidate volume, and the standing worklist |
| `duplicate_graph_signals.py` | Does graph structure tell real duplicates from same-venue coincidences? Needs a cached slice |
| `union_view_checks.py` | What the union view says once inferred edges sit beside source and derived ones: coverage lift by market and category, junk checks, proposals that clash with a same-night booking (mostly duplicates), and venue or city history behind each proposal |

```bash
uv run python bin/redirect_transfer_coverage.py
uv run python bin/redirect_transfer_coverage.py --keep-sinks   # what deletion placeholders cost
uv run python bin/redirect_transfer_coverage.py --conflicts    # are multi-donor clusters really in conflict?
uv run python bin/redirect_duplicate_precision.py
uv run python bin/duplicate_name_reach.py
uv run python bin/duplicate_name_reach.py --naive              # the every-row over-count, for comparison
uv run python bin/duplicate_detection_backtest.py
uv run python bin/duplicate_detection_backtest.py --year 2023
uv run python bin/junk_nodes.py                              # writes junk_nodes.csv
uv run python bin/union_view_checks.py                       # reads dbt_dev.bridge_graph_edges

uv run event-graph graph build --country NL --save slices/nl.pkl   # once, ~6 min
uv run python bin/duplicate_graph_signals.py --slice slices/nl.pkl --country NL
```

`_redirects.py` is the shared loader: redirect pairs, transitive closure, `dim_events`
facts, and the sink-title exclusion that stops deletion placeholders fusing clusters.
Sinks are excluded *before* the closure runs, because dropping one has to split the
cluster it fused rather than just disappear from it.

Two counting rules these scripts exist to get right, both of which produced a wrong answer
first: a redirected row is the *dead* side of a merge, so filling an attribute on it is a
write nobody reads; and a name collision against rows that are already redirected counts
work ops has finished, not work outstanding.
