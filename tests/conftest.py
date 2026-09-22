from datetime import UTC, datetime

import pytest

from event_graph.graph import (
    Edge,
    InMemoryGraphSource,
    Node,
    NodeType,
    Predicate,
    SourceClass,
)
from event_graph.graph.ontology import deterministic_edge_id

OBSERVED_AT = datetime(2026, 9, 22, tzinfo=UTC)


def make_node(node_type: NodeType, key: str, label: str) -> Node:
    return Node(
        node_id=f"{node_type.value}:{key}",
        node_type=node_type,
        natural_key=key,
        label=label,
        data_updated_at=OBSERVED_AT,
    )


def make_edge(
    src: str,
    predicate: Predicate,
    dst: str,
    *,
    confidence: float = 0.9,
    source: str = "employee",
    source_class: SourceClass = SourceClass.ADMIN_VERIFIED,
) -> Edge:
    return Edge(
        edge_id=deterministic_edge_id(src, predicate, dst, source),
        src_node_id=src,
        predicate=predicate,
        dst_node_id=dst,
        source=source,
        source_class=source_class,
        confidence=confidence,
        observed_at=OBSERVED_AT,
    )


@pytest.fixture
def nodes() -> list[Node]:
    return [
        make_node(NodeType.EVENT, "e1", "Awakenings"),
        make_node(NodeType.EVENT, "e2", "Awakenings ADE"),
        make_node(NodeType.ARTIST, "a1", "Charlotte de Witte"),
        make_node(NodeType.ARTIST, "a2", "Amelie Lens"),
        make_node(NodeType.ARTIST, "junk", "Unbekannt"),
        make_node(NodeType.VENUE, "v1", "Ziggo Dome"),
        make_node(NodeType.CITY, "c1", "Amsterdam"),
        make_node(NodeType.GENRE, "g1", "Techno"),
    ]


@pytest.fixture
def edges() -> list[Edge]:
    return [
        make_edge("artist:a1", Predicate.PERFORMS_AT, "event:e1"),
        make_edge("artist:junk", Predicate.PERFORMS_AT, "event:e1"),
        make_edge("artist:junk", Predicate.PERFORMS_AT, "event:e2"),
        make_edge("event:e1", Predicate.HELD_AT, "venue:v1"),
        make_edge("event:e2", Predicate.HELD_AT, "venue:v1"),
        make_edge("venue:v1", Predicate.IN_CITY, "city:c1"),
        make_edge(
            "artist:a1",
            Predicate.HAS_GENRE,
            "genre:g1",
            confidence=0.5,
            source="artist_tags",
            source_class=SourceClass.SYSTEM_RULE,
        ),
        make_edge(
            "artist:a1",
            Predicate.SIMILAR_TO,
            "artist:a2",
            confidence=0.7,
            source="chartmetric",
            source_class=SourceClass.THIRD_PARTY,
        ),
        make_edge(
            "event:e1",
            Predicate.SAME_AS,
            "event:e2",
            source="event_redirect",
            source_class=SourceClass.ADMIN_VERIFIED,
        ),
        # points at a node the slice does not hold, like the ~2.8k dangling rows in prod
        make_edge("artist:ghost", Predicate.PERFORMS_AT, "event:e1"),
    ]


@pytest.fixture
def source(nodes: list[Node], edges: list[Edge]) -> InMemoryGraphSource:
    return InMemoryGraphSource(nodes, edges, description="fixture")
