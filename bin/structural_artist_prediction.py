"""Can graph structure alone predict who plays an event, with no name to go on?

Mask-and-recover: take past events whose artists are known, hide those links, and rank
every artist by personalized PageRank from the event -- a random walk that restarts at the
event and spreads through its venue, promoter, series, import and the acts around them.
The walk never crosses the hidden links. It passes through entity nodes only (D10): a
genre or a city is a label shared by millions of events and would reach everything.

Reported as lift over two baselines (D7): the most-linked artist overall, and the stronger
single signal, the artist most often booked at this venue. Beating the second is what H1
asks of the graph; beating only the first means structure is the venue lookup again.

Two limits. The graph carries no dates, so an artist's *later* shows at the venue count as
history -- recall is an upper bound for what an upcoming event would get. And no name is
used on purpose: this measures structure, not linking.

    uv run python bin/structural_artist_prediction.py --slice slices/nl.pkl --country NL
"""

import random
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import click

from event_graph.clickhouse import get_client
from event_graph.graph import EventGraph, Predicate
from event_graph.graph.store import load_graph
from event_graph.linking.neighbourhood import Adjacency, build_adjacency, personalized_pagerank

KS: Final = (1, 5, 10, 50)


@dataclass(slots=True)
class Recall:
    n: int = 0
    hits: Counter[int] = field(default_factory=Counter)
    reciprocal_rank: float = 0.0
    reachable: int = 0

    def add(self, ranked: Sequence[int], truth: frozenset[int]) -> None:
        self.n += 1
        rank = next((i for i, a in enumerate(ranked, 1) if a in truth), None)
        if rank is None:
            return
        self.reachable += 1
        self.reciprocal_rank += 1 / rank
        for k in KS:
            self.hits[k] += rank <= k

    def row(self, name: str) -> str:
        cells = "  ".join(f"{self.hits[k] / self.n:6.1%}" for k in KS)
        return (
            f"  {name:<30} {cells}  {self.reciprocal_rank / self.n:6.3f}  "
            f"{self.reachable / self.n:6.1%}"
        )


def label(graph: EventGraph, adjacency: Adjacency, node: int) -> str:
    return graph.node(adjacency.ids[node]).label


def top(scores: dict[int, float], keep: Callable[[int], bool], k: int) -> list[int]:
    return [n for n, _ in sorted(scores.items(), key=lambda kv: -kv[1]) if keep(n)][:k]


@click.command()
@click.option("--slice", "slice_path", required=True, type=click.Path(path_type=Path))
@click.option("--country", required=True, help="Market the slice was built for, e.g. NL.")
@click.option("--events", "sample_size", default=600, type=int, help="Test events to mask.")
@click.option("--alpha", default=0.15, type=float, help="Restart probability.")
@click.option("--steps", default=4, type=int, help="Walk length; artists sit 3 hops out.")
@click.option(
    "--without",
    "without",
    multiple=True,
    type=click.Choice([p.value for p in Predicate]),  # noqa
    help="Leave a predicate out of the walk, to see what it carries. Repeatable.",
)
def main(
    slice_path: Path,
    country: str,
    sample_size: int,
    alpha: float,
    steps: int,
    without: tuple[str, ...],
) -> None:
    rows = get_client().query(
        """
        SELECT concat('event:', event_id)
        FROM dbt.dim_events
        WHERE event_country_iso = {country:String}
          AND event_start_at < now() AND event_start_at > now() - INTERVAL 3 YEAR
          AND coalesce(event_is_redirected, 0) = 0 AND event_has_artists = 1
          AND ifNull(event_category, '') != 'festivals'
        ORDER BY cityHash64(event_id)
        LIMIT {limit:UInt32}
        """,
        parameters={"country": country.upper(), "limit": sample_size * 3},
    ).result_rows

    graph = load_graph(slice_path).graph
    started = time.monotonic()
    adjacency = build_adjacency(graph, frozenset(Predicate(p) for p in without))
    click.echo(
        f"adjacency: {len(adjacency.ids):,} entity nodes in {time.monotonic() - started:.0f}s",
        err=True,
    )

    tests = [
        adjacency.index[node_id]
        for (node_id,) in rows
        if node_id in adjacency.index and adjacency.artists_of.get(adjacency.index[node_id])
    ][:sample_size]
    popular = [a for a, _ in adjacency.performs.most_common(max(KS))]

    results = {
        "most-linked artist overall": Recall(),
        "most booked at this venue": Recall(),
        "personalized PageRank": Recall(),
    }
    with_history = {"venue baseline": Recall(), "personalized PageRank": Recall()}
    rng = random.Random(7)
    examples: list[tuple[str, list[str], list[str]]] = []
    started = time.monotonic()

    for event in tests:
        truth = adjacency.artists_of[event]
        scores = personalized_pagerank(adjacency, event, truth, alpha=alpha, steps=steps)
        ranked = top(scores, lambda n: adjacency.is_artist[n], max(KS))

        at_venue: Counter[int] = Counter()
        for venue in adjacency.venues_of.get(event, ()):
            for other in adjacency.neighbours[venue]:
                if other != event:
                    at_venue.update(adjacency.artists_of.get(other, ()))
        venue_ranked = [
            a for a, _ in sorted(at_venue.items(), key=lambda kv: (-kv[1], -adjacency.performs[kv[0]]))
        ][: max(KS)]

        results["most-linked artist overall"].add(popular, truth)
        results["most booked at this venue"].add(venue_ranked, truth)
        results["personalized PageRank"].add(ranked, truth)
        if at_venue:
            with_history["venue baseline"].add(venue_ranked, truth)
            with_history["personalized PageRank"].add(ranked, truth)
        if len(examples) < 6 and rng.random() < 0.05:
            examples.append(
                (
                    label(graph, adjacency, event),
                    [label(graph, adjacency, a) for a in truth][:3],
                    [label(graph, adjacency, a) for a in ranked[:5]],
                )
            )

    click.echo(
        f"\n{len(tests):,} masked {country} events · alpha {alpha} · {steps} steps · "
        f"without {','.join(without) or 'nothing'} · "
        f"{(time.monotonic() - started) / max(1, len(tests)):.2f}s per event"
    )
    header = "  ".join(f"{'@' + str(k):>6}" for k in KS)
    click.echo(f"\n  {'recall (a true artist in top k)':<30} {header}  {'MRR':>6}  {'found':>6}")
    for name, recall in results.items():
        click.echo(recall.row(name))
    n = with_history["venue baseline"].n
    click.echo(f"\n  only events whose venue has other artist history ({n:,} of {len(tests):,})")
    for name, recall in with_history.items():
        click.echo(recall.row(name))

    click.echo("\n  examples: event · true artists · PageRank top 5")
    for event_label, truth_labels, predicted in examples:
        click.echo(f"    {event_label[:50]}")
        click.echo(f"      true: {', '.join(truth_labels)}")
        click.echo(f"      ppr : {', '.join(predicted)}")


if __name__ == "__main__":
    main()
