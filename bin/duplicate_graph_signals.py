"""Does graph structure tell real duplicates apart from same-venue coincidences?

Two events at the same venue on the same date are a duplicate candidate about one time in
six. String matching cannot improve on that for the 38.6% of real merges whose titles
differ, which is the case this project exists for: the claim is that several individually
weak structural signals combine into one confident answer.

This tests that claim directly. Candidate pairs come from ClickHouse; every feature is
computed by walking the in-memory projection, and each is reported as the redirect rate
among the pairs it fires on, against the base rate for all candidates. A feature that does
not move the rate is not a signal, however plausible it sounded.

    uv run event-graph graph build --country NL --save slices/nl.pkl   # once
    uv run python bin/duplicate_graph_signals.py --slice slices/nl.pkl --country NL
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import click

sys.path.insert(0, str(Path(__file__).parent))

from _redirects import load_event_facts, load_redirect_pairs, sink_event_ids  # noqa: E402

from event_graph.clickhouse import get_client  # noqa: E402
from event_graph.graph import Direction, EventGraph, NodeType, Predicate  # noqa: E402
from event_graph.graph.store import load_graph  # noqa: E402

# a venue with more events than this on one date is multi-room or a festival; every pair
# inside it would be noise
MAX_GROUP = 12


@dataclass(frozen=True, slots=True)
class EventSignals:
    """Everything about one event that a pairwise feature is computed from."""

    artists: frozenset[str]
    similar_artists: frozenset[str]
    brands: frozenset[str]
    series: frozenset[str]
    neighbourhood: frozenset[str]


def collect(graph: EventGraph, node_id: str, *, max_degree: int) -> EventSignals:
    artists = frozenset(
        node.node_id
        for _, node in graph.incident(
            node_id, predicates=frozenset({Predicate.PERFORMS_AT}), direction=Direction.IN
        )
    )
    similar: set[str] = set()
    for artist in artists:
        if graph.degree(artist) > max_degree:
            continue
        for _, node in graph.incident(
            artist, predicates=frozenset({Predicate.SIMILAR_TO}), direction=Direction.BOTH
        ):
            similar.add(node.node_id)

    def out(predicate: Predicate) -> frozenset[str]:
        return frozenset(
            node.node_id
            for _, node in graph.incident(
                node_id, predicates=frozenset({predicate}), direction=Direction.OUT
            )
        )

    neighbourhood = frozenset(
        reached.node.node_id
        for reached in graph.expand(node_id, hops=2, max_degree=max_degree)
        if reached.node.node_type is not NodeType.EVENT
    )
    return EventSignals(
        artists=artists,
        similar_artists=frozenset(similar),
        brands=out(Predicate.PROMOTED_BY),
        series=out(Predicate.SERIES_OF),
        neighbourhood=neighbourhood,
    )


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


FEATURES: dict[str, Callable[[EventSignals, EventSignals], bool]] = {
    "shared artist (1 hop)": lambda a, b: bool(a.artists & b.artists),
    "similar_to link, no shared artist (2 hops)": lambda a, b: not (a.artists & b.artists)
    and bool((a.artists & b.similar_artists) or (b.artists & a.similar_artists)),
    "shared organizer brand": lambda a, b: bool(a.brands & b.brands),
    "shared series": lambda a, b: bool(a.series & b.series),
    "neighbourhood overlap > 0.1": lambda a, b: jaccard(a.neighbourhood, b.neighbourhood) > 0.1,
    "neighbourhood overlap > 0.3": lambda a, b: jaccard(a.neighbourhood, b.neighbourhood) > 0.3,
    "both artist-less": lambda a, b: not a.artists and not b.artists,
}


@click.command()
@click.option("--slice", "slice_path", required=True, type=click.Path(path_type=Path))
@click.option("--country", required=True, help="Market the slice was built for, e.g. NL.")
@click.option("--max-degree", default=2000, type=int, help="Do not walk out of hubs.")
@click.option("--max-pairs", default=40000, type=int, help="Cap candidate pairs scored.")
def main(slice_path: Path, country: str, max_degree: int, max_pairs: int) -> None:
    client = get_client()

    redirects = load_redirect_pairs(client)
    facts = load_event_facts(client)
    component_of = {
        member: index
        for index, component in enumerate(redirects.components(exclude=sink_event_ids(facts)))
        for member in component
    }

    groups = client.query(
        """
        SELECT groupArray(event_id) AS ids
        FROM dbt.dim_events
        WHERE event_country_iso = {country:String}
          AND event_start_at < now() AND venue_id != 0
        GROUP BY venue_id, toDate(event_start_at)
        HAVING length(ids) BETWEEN 2 AND {max_group:UInt8}
        """,
        parameters={"country": country, "max_group": MAX_GROUP},
    ).result_rows
    candidates = [
        (str(left), str(right))
        for (ids,) in groups
        for left, right in combinations(sorted(str(i) for i in ids), 2)
    ]
    click.echo(
        f"candidate pairs in {country}: {len(candidates):,} from {len(groups):,} "
        f"(venue, date) groups",
        err=True,
    )

    graph = load_graph(slice_path).graph
    click.echo(f"slice: {graph.node_count:,} nodes, {graph.edge_count:,} edges", err=True)

    cache: dict[str, EventSignals] = {}

    def signals(event_id: str) -> EventSignals | None:
        node_id = f"event:{event_id}"
        if node_id not in cache:
            if node_id not in graph:
                return None
            cache[node_id] = collect(graph, node_id, max_degree=max_degree)
        return cache[node_id]

    hits: dict[str, list[int]] = {name: [0, 0] for name in FEATURES}
    scored = redirected = 0

    for left_id, right_id in candidates[:max_pairs]:
        left, right = signals(left_id), signals(right_id)
        if left is None or right is None:
            continue
        scored += 1
        component = component_of.get(left_id)
        is_duplicate = component is not None and component == component_of.get(right_id)
        redirected += is_duplicate
        for name, fires in FEATURES.items():
            if fires(left, right):
                hits[name][0] += 1
                hits[name][1] += is_duplicate

    if not scored:
        raise click.UsageError(
            f"no candidate pair had both events in {slice_path}. "
            f"Is the slice built for --country {country}?"
        )

    base = redirected / scored
    click.echo(f"\npairs scored: {scored:,} · redirected together: {redirected:,} ({base:.1%})")
    click.echo(f"\n  {'feature':<44} {'fires':>8} {'redirected':>11} {'lift':>7}")
    for name, (fired, confirmed) in hits.items():
        if not fired:
            click.echo(f"  {name:<44} {0:>8} {'--':>11} {'--':>7}")
            continue
        rate = confirmed / fired
        click.echo(f"  {name:<44} {fired:>8,} {rate:>10.1%} {rate / base:>6.2f}x")


if __name__ == "__main__":
    main()
