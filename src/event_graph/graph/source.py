import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Sequence
from typing import Any, Final

from clickhouse_connect.driver.client import Client
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from event_graph.config import GraphTablesConfig, get_config
from event_graph.graph.edges import Edge
from event_graph.graph.errors import ConfigurationError
from event_graph.graph.nodes import Node
from event_graph.graph.ontology import ENTITY_NODE_TYPES, NodeType, Predicate, SourceClass

_IDENTIFIER: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?")


def _identifier(value: str, what: str) -> str:
    """Guard a table or column name, which cannot be passed as a query parameter."""
    if not _IDENTIFIER.fullmatch(value):
        raise ConfigurationError(
            f"{what} {value!r} is not a valid ClickHouse identifier; "
            "expected `table` or `database.table`"
        )
    return value


class GraphSlice(BaseModel):
    """Which part of the canonical tables to project.

    The full graph is 7.7M nodes and 19.3M edges, which fits in memory but is slow to
    iterate on. A slice is how a three-day build stays interactive -- and `country` is
    there because the organizer-brand signal is present in NL and absent in ES, so every
    number has to be reported per market rather than pooled.

    `closure_hops` is how far past the market's events the slice reaches, and the default
    of 2 is not arbitrary. At one hop you get the events and everything directly attached
    to them, but no `similar_to`: those hang off artists, one hop further out. Ranking
    combines title match, venue history and similar-artist co-billing, so a one-hop slice
    silently removes one of the three directions.

    `closure_through` is what keeps that second hop finite. Classification nodes are hubs
    by construction -- 55 genre nodes carry 3.2M `has_genre` edges -- so a closure that
    expands through them drags in the entire graph: measured on Iceland, 853 events became
    1.19M nodes in 201s. They are still loaded, as leaves; the walk just stops at them.
    """

    model_config = ConfigDict(frozen=True)

    node_types: frozenset[NodeType] | None = None
    predicates: frozenset[Predicate] | None = None
    source_classes: frozenset[SourceClass] | None = None
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    country: str | None = Field(default=None, description="ISO-2, e.g. NL or ES")
    closure_hops: int = Field(default=2, ge=1, le=3)
    closure_through: frozenset[NodeType] = Field(default=ENTITY_NODE_TYPES)
    max_nodes: int | None = Field(default=None, gt=0)
    max_edges: int | None = Field(default=None, gt=0)

    def describe(self) -> str:
        parts: list[str] = []
        if self.country:
            parts.append(f"country={self.country}")
            parts.append(f"closure_hops={self.closure_hops}")
            if self.closure_through != ENTITY_NODE_TYPES:
                parts.append(f"closure_through={','.join(sorted(self.closure_through))}")
        if self.node_types:
            parts.append(f"node_types={','.join(sorted(self.node_types))}")
        if self.predicates:
            parts.append(f"predicates={','.join(sorted(self.predicates))}")
        if self.source_classes:
            parts.append(f"source_classes={','.join(sorted(self.source_classes))}")
        if self.min_confidence:
            parts.append(f"min_confidence={self.min_confidence}")
        if self.max_nodes:
            parts.append(f"max_nodes={self.max_nodes}")
        if self.max_edges:
            parts.append(f"max_edges={self.max_edges}")
        return ", ".join(parts) or "full graph"


class GraphSource(ABC):
    """A source of nodes and edges for one build."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable provenance, recorded in the build metadata."""

    @property
    def slice(self) -> GraphSlice:
        """The slice this source represents. Sources that cannot be sliced project it all."""
        return GraphSlice()

    @abstractmethod
    def nodes(self) -> Iterator[Node]:
        """Yield every node in the slice."""

    @abstractmethod
    def edges(self) -> Iterator[Edge]:
        """Yield every edge in the slice."""


class InMemoryGraphSource(GraphSource):
    """A source backed by lists, for tests and fixtures."""

    def __init__(
        self,
        nodes: Sequence[Node],
        edges: Sequence[Edge],
        description: str = "in-memory",
    ) -> None:
        self._nodes = nodes
        self._edges = edges
        self._description = description

    @property
    def description(self) -> str:
        return self._description

    def nodes(self) -> Iterator[Node]:
        return iter(self._nodes)

    def edges(self) -> Iterator[Edge]:
        return iter(self._edges)


class ClickHouseGraphSource(GraphSource):
    """Streams the canonical tables out of ClickHouse, one block at a time.

    Reads `bridge_graph_edges_source`, never the union view: inference is fed source edges
    only, so a wrong guess in one run cannot become evidence for the next.
    """

    def __init__(
        self,
        graph_slice: GraphSlice | None = None,
        *,
        client: Client | None = None,
        tables: GraphTablesConfig | None = None,
    ) -> None:
        self._slice = graph_slice or GraphSlice()
        self._injected_client = client
        self._tables = tables or get_config().graph

    @property
    def slice(self) -> GraphSlice:
        return self._slice

    @property
    def description(self) -> str:
        return f"clickhouse {self._tables.edges_table} ({self._slice.describe()})"

    @property
    def _client(self) -> Client:
        if self._injected_client is None:
            from event_graph.clickhouse import get_client

            self._injected_client = get_client()
        return self._injected_client

    def nodes(self) -> Iterator[Node]:
        sql, parameters = self._nodes_query()
        yield from self._stream(sql, parameters, Node.from_row)

    def edges(self) -> Iterator[Edge]:
        sql, parameters = self._edges_query()
        yield from self._stream(sql, parameters, Edge.from_row)

    def _stream[T](
        self,
        sql: str,
        parameters: dict[str, Any],
        parse: Callable[[tuple[Any, ...]], T],
    ) -> Iterator[T]:
        with self._client.query_row_block_stream(sql, parameters=parameters) as stream:
            for block in stream:
                for row in block:
                    try:
                        yield parse(row)
                    except ValidationError as error:
                        raise ConfigurationError(
                            f"row {row!r} does not fit the ontology in ontology.py: {error}.\n"
                            "Either the built tables gained a value this repo does not know, "
                            "or the table names in config.py point somewhere unexpected."
                        ) from error

    def _nodes_query(self) -> tuple[str, dict[str, Any]]:
        nodes_table = _identifier(self._tables.nodes_table, "nodes_table")
        id_column = _identifier(self._tables.node_id_column, "node_id_column")
        columns = ", ".join(
            f"{id_column} AS node_id" if column == "node_id" else column
            for column in Node.COLUMNS
        )

        conditions: list[str] = []
        parameters: dict[str, Any] = {}
        if self._slice.node_types:
            conditions.append("node_type IN {node_types:Array(String)}")
            parameters["node_types"] = sorted(t.value for t in self._slice.node_types)

        prelude = ""
        if self._slice.country:
            ctes, _, node_relation, closure_parameters = self._closure_chain()
            parameters |= closure_parameters
            prelude = f"WITH {', '.join(ctes)} "
            conditions.append(f"{id_column} IN (SELECT node_id FROM {node_relation})")

        sql = f"{prelude}SELECT {columns} FROM {nodes_table} WHERE {self._and(conditions)}"
        if self._slice.max_nodes:
            sql += f" LIMIT {int(self._slice.max_nodes)}"
        return sql, parameters

    def _edges_query(self) -> tuple[str, dict[str, Any]]:
        edges_table = _identifier(self._tables.edges_table, "edges_table")
        columns = ", ".join(Edge.COLUMNS)

        if self._slice.country:
            ctes, edge_relation, _, parameters = self._closure_chain()
            sql = f"WITH {', '.join(ctes)} SELECT {columns} FROM {edge_relation}"
        else:
            conditions, parameters = self._edge_conditions()
            sql = f"SELECT {columns} FROM {edges_table} WHERE {self._and(conditions)}"

        if self._slice.max_edges:
            sql += f" LIMIT {int(self._slice.max_edges)}"
        return sql, parameters

    def _closure_chain(self) -> tuple[list[str], str, str, dict[str, Any]]:
        """Build the CTE chain that walks `closure_hops` out from the market's events.

        Returns the CTE definitions, the name of the relation holding the edges to load,
        the name of the relation holding the nodes to load, and the query parameters.

        Only the last hop's edge relation is loaded, which is correct because each frontier
        contains the one before it, so each hop's edges are a superset of the previous
        hop's. That containment is kept explicitly below rather than left to follow from
        `closure_through` happening to include the seed's node type.
        """
        edges_table = _identifier(self._tables.edges_table, "edges_table")
        columns = ", ".join(Edge.COLUMNS)
        conditions, parameters = self._edge_conditions()
        parameters["country"] = self._slice.country

        ctes = [self._seed_events_cte()]
        frontier = "seed_events"
        node_relation = "seed_events"
        edge_relation = ""
        if self._slice.closure_hops > 1:
            parameters["closure_through"] = sorted(t.value for t in self._slice.closure_through)

        for hop in range(1, self._slice.closure_hops + 1):
            edge_relation = f"closure_edges_{hop}"
            reached = f"closure_nodes_{hop}"
            incident = (
                f"(src_node_id IN (SELECT node_id FROM {frontier})"
                f" OR dst_node_id IN (SELECT node_id FROM {frontier}))"
            )
            ctes.append(
                f"{edge_relation} AS ("
                f" SELECT {columns} FROM {edges_table}"
                f" WHERE {self._and([*conditions, incident])})"
            )
            ctes.append(
                f"{reached} AS ("
                f" SELECT node_id FROM {node_relation}"
                f" UNION DISTINCT SELECT src_node_id AS node_id FROM {edge_relation}"
                f" UNION DISTINCT SELECT dst_node_id AS node_id FROM {edge_relation})"
            )
            node_relation = reached

            if hop < self._slice.closure_hops:
                previous_frontier = frontier
                frontier = f"closure_frontier_{hop}"
                ctes.append(
                    f"{frontier} AS ("
                    f" SELECT node_id FROM {reached}"
                    " WHERE splitByChar(':', node_id)[1] IN {closure_through:Array(String)}"
                    f" UNION DISTINCT SELECT node_id FROM {previous_frontier})"
                )

        return ctes, edge_relation, node_relation, parameters

    def _edge_conditions(self) -> tuple[list[str], dict[str, Any]]:
        conditions: list[str] = []
        parameters: dict[str, Any] = {}

        if self._slice.predicates:
            conditions.append("predicate IN {predicates:Array(String)}")
            parameters["predicates"] = sorted(p.value for p in self._slice.predicates)
        if self._slice.source_classes:
            conditions.append("source_class IN {source_classes:Array(String)}")
            parameters["source_classes"] = sorted(c.value for c in self._slice.source_classes)
        if self._slice.min_confidence:
            conditions.append("confidence >= {min_confidence:Float32}")
            parameters["min_confidence"] = self._slice.min_confidence
        return conditions, parameters

    def _seed_events_cte(self) -> str:
        """Event nodes in the requested market, joined back to the events dim."""
        return (
            "seed_events AS ("
            f"  SELECT n.{_identifier(self._tables.node_id_column, 'node_id_column')} AS node_id"
            f"  FROM {_identifier(self._tables.nodes_table, 'nodes_table')} AS n"
            f"  INNER JOIN {_identifier(self._tables.events_table, 'events_table')} AS ev"
            f"    ON n.natural_key = ev.{_identifier(self._tables.events_key_column, 'events_key_column')}"
            "   WHERE n.node_type = 'event'"
            f"    AND ev.{_identifier(self._tables.events_country_column, 'events_country_column')}"
            "        = {country:String}"
            ")"
        )

    @staticmethod
    def _and(conditions: Sequence[str]) -> str:
        return " AND ".join(conditions) if conditions else "1"
