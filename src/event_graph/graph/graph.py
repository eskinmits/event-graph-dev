"""The in-memory projection.

A disposable lens over the canonical ClickHouse tables (D1). It holds no unique
information: delete it, rebuild it, and you are exactly where you were. Nothing is ever
written here -- inference results go back to ClickHouse and arrive on the next rebuild.
"""

import logging
from collections import Counter, deque
from collections.abc import Collection, Iterator
from dataclasses import dataclass, field
from enum import StrEnum

import rustworkx as rx

from event_graph.graph.edges import Edge
from event_graph.graph.errors import NodeNotFoundError, UnknownNodeError
from event_graph.graph.nodes import Node
from event_graph.graph.ontology import NodeType, Predicate, validate_endpoints

logger = logging.getLogger(__name__)


class Direction(StrEnum):
    """Which way to walk an edge.

    `BOTH` is the default because the useful walks run against the arrows as often as with
    them: an event reaches its artists through `performs_at` backwards.
    """

    OUT = "out"
    IN = "in"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class Reached:
    """A node the walk arrived at, and the edges it took to get there."""

    node: Node
    hops: int
    path: tuple[Edge, ...]

    @property
    def confidence(self) -> float:
        """The weakest link on the path -- a chain is only as good as its worst edge."""
        return min((edge.confidence for edge in self.path), default=1.0)

    def explain(self) -> str:
        """One line naming every edge that justifies this node being here."""
        return " · ".join(str(edge) for edge in self.path) or str(self.node)


@dataclass(frozen=True, slots=True)
class Neighbourhood:
    """The result of a bounded walk: what we found, and why each thing is in it."""

    root: Node
    reached: dict[str, Reached] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.reached)

    def __iter__(self) -> Iterator[Reached]:
        return iter(sorted(self.reached.values(), key=lambda r: (r.hops, r.node.node_id)))

    def at_hop(self, hops: int) -> list[Reached]:
        return [r for r in self if r.hops == hops]

    def of_type(self, node_type: NodeType) -> list[Reached]:
        return [r for r in self if r.node.node_type is node_type]


class EventGraph:
    """Typed, provenance-carrying graph of events and their metadata.

    rustworkx indexes nodes by integer, so the projection keeps a `node_id -> index` map
    and translates back before anything is written to ClickHouse.
    """

    def __init__(self) -> None:
        self._graph: rx.PyDiGraph[Node, Edge] = rx.PyDiGraph(multigraph=True)
        self._index_by_node_id: dict[str, int] = {}

    def __len__(self) -> int:
        return self._graph.num_nodes()

    def __contains__(self, node_id: str) -> bool:
        return node_id in self._index_by_node_id

    @property
    def node_count(self) -> int:
        return self._graph.num_nodes()

    @property
    def edge_count(self) -> int:
        return self._graph.num_edges()

    def add_node(self, node: Node) -> int:
        """Add a node, or return the index of the one already held under that id."""
        existing = self._index_by_node_id.get(node.node_id)
        if existing is not None:
            return existing
        index = self._graph.add_node(node)
        self._index_by_node_id[node.node_id] = index
        return index

    def add_edge(self, edge: Edge) -> int:
        """Add an edge between two nodes already in the graph.

        Raises `UnknownNodeError` when either endpoint is missing. That is not a defensive
        check: ~2.8k built edges point at rows the dims exclude (deleted artists, test
        events), so the builder counts them rather than letting them fail the run.
        """
        src = self._index_by_node_id.get(edge.src_node_id)
        dst = self._index_by_node_id.get(edge.dst_node_id)
        if src is None or dst is None:
            missing = edge.src_node_id if src is None else edge.dst_node_id
            raise UnknownNodeError(f"edge {edge.edge_id} references unknown node {missing!r}")
        validate_endpoints(
            edge.predicate, self._graph[src].node_type, self._graph[dst].node_type
        )
        return self._graph.add_edge(src, dst, edge)

    def node(self, node_id: str) -> Node:
        """Look up a node, raising a readable error when it is absent."""
        index = self._index_by_node_id.get(node_id)
        if index is None:
            raise NodeNotFoundError(f"node {node_id!r} is not in the graph")
        return self._graph[index]

    def degree(self, node_id: str) -> int:
        index = self._require_index(node_id)
        return self._graph.in_degree(index) + self._graph.out_degree(index)

    def incident(
        self,
        node_id: str,
        *,
        predicates: frozenset[Predicate] | None = None,
        direction: Direction = Direction.BOTH,
        min_confidence: float = 0.0,
    ) -> Iterator[tuple[Edge, Node]]:
        """Yield each matching edge on a node together with the node at its other end."""
        index = self._require_index(node_id)
        for edge, other_index in self._adjacent(index, direction):
            if predicates is not None and edge.predicate not in predicates:
                continue
            if edge.confidence < min_confidence:
                continue
            yield edge, self._graph[other_index]

    def expand(
        self,
        node_id: str,
        *,
        hops: int = 2,
        predicates: frozenset[Predicate] | None = None,
        direction: Direction = Direction.BOTH,
        min_confidence: float = 0.0,
        max_degree: int | None = None,
        through: frozenset[NodeType] | None = None,
    ) -> Neighbourhood:
        """Walk outwards from a node, keeping the path that first reached each neighbour.

        Two guards stop the walk running away, and neither excludes a node from the result
        -- they only stop it being expanded *through*:

        - `max_degree` by how connected a node turned out to be;
        - `through` by node type, which is the same rule `GraphSlice.closure_through`
          applies in SQL. Pass `ENTITY_NODE_TYPES` and a walk will reach a genre but not
          continue out of it into every other event sharing it.
        """
        root_index = self._require_index(node_id)
        root = self._graph[root_index]
        reached: dict[str, Reached] = {node_id: Reached(root, 0, ())}
        queue: deque[tuple[int, int, tuple[Edge, ...]]] = deque([(root_index, 0, ())])

        while queue:
            index, depth, path = queue.popleft()
            if depth >= hops:
                continue
            if index != root_index:
                if through is not None and self._graph[index].node_type not in through:
                    continue
                if max_degree is not None:
                    if self._graph.in_degree(index) + self._graph.out_degree(index) > max_degree:
                        continue
            for edge, next_index in self._adjacent(index, direction):
                if predicates is not None and edge.predicate not in predicates:
                    continue
                if edge.confidence < min_confidence:
                    continue
                neighbour = self._graph[next_index]
                if neighbour.node_id in reached:
                    continue
                next_path = (*path, edge)
                reached[neighbour.node_id] = Reached(neighbour, depth + 1, next_path)
                queue.append((next_index, depth + 1, next_path))

        return Neighbourhood(root=root, reached=reached)

    def components(
        self,
        predicates: frozenset[Predicate],
        *,
        min_confidence: float = 0.0,
    ) -> list[frozenset[str]]:
        """Connected components over the given predicates only.

        This is the `same_as` closure that produces dedup clusters. Nodes touched by no
        matching edge are not returned -- a cluster of one is not a cluster. Filter junk
        hubs before calling this, or unrelated clusters fuse through them.
        """
        projection: rx.PyGraph[str, None] = rx.PyGraph(multigraph=False)
        local_index: dict[int, int] = {}

        for src, dst, edge in self._graph.weighted_edge_list():
            if edge.predicate not in predicates or edge.confidence < min_confidence:
                continue
            ends = []
            for index in (src, dst):
                local = local_index.get(index)
                if local is None:
                    local = projection.add_node(self._graph[index].node_id)
                    local_index[index] = local
                ends.append(local)
            projection.add_edge(ends[0], ends[1], None)

        return [
            frozenset(projection[local] for local in component)
            for component in rx.connected_components(projection)
        ]

    def prune_hubs(
        self,
        *,
        max_degree: int,
        node_types: frozenset[NodeType] | None = None,
    ) -> list[str]:
        """Drop nodes above a degree ceiling, returning the ids removed.

        Degree is only known once the graph is built, so this is the half of junk-hub
        handling that a load-time label filter cannot do.
        """
        doomed = [
            node_id
            for node_id, index in self._index_by_node_id.items()
            if (node_types is None or self._graph[index].node_type in node_types)
            and self._graph.in_degree(index) + self._graph.out_degree(index) > max_degree
        ]
        for node_id in doomed:
            self._graph.remove_node(self._index_by_node_id.pop(node_id))
        if doomed:
            logger.info("pruned %d hub nodes above degree %d", len(doomed), max_degree)
        return doomed

    def nodes(self) -> Iterator[Node]:
        """Every node held, in no particular order."""
        return iter(self._graph.nodes())

    def edges(self) -> Iterator[Edge]:
        """Every edge held, in no particular order."""
        return iter(self._graph.edges())

    def search(
        self,
        text: str,
        *,
        node_types: frozenset[NodeType] | None = None,
        limit: int = 20,
    ) -> list[Node]:
        """Find nodes whose label or natural key contains `text`, case-folded.

        Ranked exact, then prefix, then substring, so typing a full event title puts that
        event first rather than whichever row the scan happened to reach first.
        """
        needle = text.casefold().strip()
        if not needle:
            return []

        ranked: list[tuple[int, str, Node]] = []
        for node in self._graph.nodes():
            if node_types is not None and node.node_type not in node_types:
                continue
            label = node.label.casefold()
            if needle in label:
                rank = 0 if label == needle else 1 if label.startswith(needle) else 2
            elif needle in node.natural_key.casefold():
                rank = 3
            else:
                continue
            ranked.append((rank, node.label, node))

        ranked.sort(key=lambda item: (item[0], len(item[1]), item[1]))
        return [node for _, _, node in ranked[:limit]]

    def induced_edges(
        self,
        node_ids: Collection[str],
        *,
        max_degree: int | None = None,
    ) -> Iterator[Edge]:
        """Yield every edge whose endpoints are both in `node_ids`.

        A neighbourhood's paths form a tree; these are the edges that close it back into a
        graph -- two artists on the same bill, a venue's other events. `max_degree` skips
        scanning out of hubs, which is where the cost would otherwise be.
        """
        for node_id in node_ids:
            index = self._index_by_node_id.get(node_id)
            if index is None:
                continue
            if max_degree is not None and self._graph.out_degree(index) > max_degree:
                continue
            for _, dst, edge in self._graph.out_edges(index):
                if self._graph[dst].node_id in node_ids:
                    yield edge

    def node_counts(self) -> dict[NodeType, int]:
        return dict(Counter(node.node_type for node in self._graph.nodes()))

    def edge_counts(self) -> dict[Predicate, int]:
        return dict(Counter(edge.predicate for edge in self._graph.edges()))

    def _require_index(self, node_id: str) -> int:
        index = self._index_by_node_id.get(node_id)
        if index is None:
            raise NodeNotFoundError(f"node {node_id!r} is not in the graph")
        return index

    def _adjacent(self, index: int, direction: Direction) -> Iterator[tuple[Edge, int]]:
        if direction in (Direction.OUT, Direction.BOTH):
            for _, dst, edge in self._graph.out_edges(index):
                yield edge, dst
        if direction in (Direction.IN, Direction.BOTH):
            for src, _, edge in self._graph.in_edges(index):
                yield edge, src
