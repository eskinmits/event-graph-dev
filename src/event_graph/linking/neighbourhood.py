"""Which artists are structurally close to an event: personalized PageRank from it.

A walk that restarts at the event and spreads through its venue, promoter, series, import
and the acts around them. It passes through entity nodes only (D10): a genre or a city is a
label shared by millions of events and would reach everything. Measured on its own it finds
the true artist in the top 50 for a third (NL) to two fifths (ES) of events and ranks it
first 12-16% of the time -- a shortlist, not an answer. Its use is to shrink the space a
name is matched against from 1.2M artist rows to a few hundred.
"""

from collections import Counter, defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from functools import cache
from typing import Final

from event_graph.graph.graph import EventGraph
from event_graph.graph.ontology import ENTITY_NODE_TYPES, NodeType, Predicate

ALPHA: Final = 0.15
# artists sit three hops out (event -> venue -> event -> artist); a fourth adds similar_to
STEPS: Final = 4
# mass below this share of the walk is dropped, which keeps a hub's fan-out finite
PRUNE: Final = 1e-6


@dataclass(frozen=True, slots=True)
class Adjacency:
    """Entity-only neighbour lists over integer ids, built once so a walk is dict work."""

    ids: list[str]
    index: dict[str, int]
    neighbours: list[list[int]]
    is_artist: list[bool]
    artists_of: dict[int, frozenset[int]]
    venues_of: dict[int, frozenset[int]]
    performs: Counter[int]


def build_adjacency(graph: EventGraph) -> Adjacency:
    ids: list[str] = []
    index: dict[str, int] = {}
    is_artist: list[bool] = []
    for node in graph.nodes():
        if node.node_type in ENTITY_NODE_TYPES:
            index[node.node_id] = len(ids)
            ids.append(node.node_id)
            is_artist.append(node.node_type is NodeType.ARTIST)

    neighbours: list[list[int]] = [[] for _ in ids]
    artists_of: dict[int, set[int]] = defaultdict(set)
    venues_of: dict[int, set[int]] = defaultdict(set)
    performs: Counter[int] = Counter()
    for edge in graph.edges():
        src, dst = index.get(edge.src_node_id), index.get(edge.dst_node_id)
        if src is None or dst is None or src == dst:
            continue
        neighbours[src].append(dst)
        neighbours[dst].append(src)
        if edge.predicate is Predicate.PERFORMS_AT:
            artists_of[dst].add(src)
            performs[src] += 1
        elif edge.predicate is Predicate.HELD_AT:
            venues_of[src].add(dst)
    return Adjacency(
        ids,
        index,
        neighbours,
        is_artist,
        {k: frozenset(v) for k, v in artists_of.items()},
        {k: frozenset(v) for k, v in venues_of.items()},
        performs,
    )


def personalized_pagerank(
    adjacency: Adjacency,
    root: int,
    masked: Collection[int],
    *,
    alpha: float = ALPHA,
    steps: int = STEPS,
) -> dict[int, float]:
    """Truncated PPR by power iteration from one node, never crossing root <-> masked."""
    score: dict[int, float] = defaultdict(float)
    frontier: dict[int, float] = {root: 1.0}
    for _ in range(steps):
        spread: dict[int, float] = defaultdict(float)
        for node, mass in frontier.items():
            score[node] += alpha * mass
            onward = adjacency.neighbours[node]
            if node == root and masked:
                onward = [n for n in onward if n not in masked]
            if not onward:
                continue
            share = (1 - alpha) * mass / len(onward)
            if share < PRUNE:
                continue
            for neighbour in onward:
                if neighbour == root and node in masked:
                    continue
                spread[neighbour] += share
        frontier = spread
    for node, mass in frontier.items():
        score[node] += alpha * mass
    return score


@dataclass(frozen=True, slots=True)
class Neighbour:
    artist_node_id: str
    label: str
    rank: int
    score: float
    linked_events: int


class Neighbourhood:
    """Artists near an event, cached per event and mask."""

    def __init__(self, graph: EventGraph, adjacency: Adjacency | None = None) -> None:
        self.graph = graph
        self.adjacency = adjacency or build_adjacency(graph)
        self._artists = cache(self._artists_uncached)

    def artists(
        self, event_node_id: str, masked: frozenset[str], limit: int
    ) -> tuple[Neighbour, ...]:
        """The `limit` artists with most walk mass.

        `masked` artists may still be returned -- the walk just cannot reach them over their
        direct link to this event, only the way an unlinked act would be reached.
        """
        return self._artists(event_node_id, masked, limit)

    def _artists_uncached(
        self, event_node_id: str, masked: frozenset[str], limit: int
    ) -> tuple[Neighbour, ...]:
        root = self.adjacency.index.get(event_node_id)
        if root is None:
            return ()
        hidden = {self.adjacency.index[m] for m in masked if m in self.adjacency.index}
        scores = personalized_pagerank(self.adjacency, root, hidden)
        ranked = sorted(
            ((node, mass) for node, mass in scores.items() if self.adjacency.is_artist[node]),
            key=lambda item: -item[1],
        )[:limit]
        return tuple(
            Neighbour(
                artist_node_id=self.adjacency.ids[node],
                label=self.graph.node(self.adjacency.ids[node]).label,
                rank=rank,
                score=mass,
                linked_events=self.adjacency.performs[node],
            )
            for rank, (node, mass) in enumerate(ranked, 1)
        )
