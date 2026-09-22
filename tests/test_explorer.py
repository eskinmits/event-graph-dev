from datetime import UTC, datetime
from pathlib import Path

import pytest

from event_graph.graph import (
    BuildMetadata,
    Direction,
    EventGraph,
    GraphBuilder,
    GraphSlice,
    InMemoryGraphSource,
    NodeType,
)
from event_graph.graph.store import load_graph, save_graph
from event_graph.web import Explorer


@pytest.fixture
def explorer(source: InMemoryGraphSource) -> Explorer:
    graph, metadata, report = GraphBuilder(source).build()
    return Explorer(graph, metadata, report)


def test_meta_payload_reports_what_the_slice_holds(explorer: Explorer) -> None:
    payload = explorer.meta_payload

    assert payload["nodes"] == explorer.graph.node_count
    assert payload["edges"] == explorer.graph.edge_count
    assert payload["node_counts"]["event"] == 2
    assert payload["node_types"][0] == NodeType.EVENT.value


def test_meta_payload_is_cached_not_recomputed(explorer: Explorer) -> None:
    assert explorer.meta_payload is explorer.meta_payload


def test_search_ranks_exact_matches_first(explorer: Explorer) -> None:
    results = explorer.search("awakenings", node_types=None, limit=10)["results"]

    assert [result["label"] for result in results] == ["Awakenings", "Awakenings ADE"]
    assert results[0]["node_id"] == "event:e1"


def test_search_can_restrict_to_a_node_type(explorer: Explorer) -> None:
    # "o" also matches an artist and a genre, so an empty filter would show them too
    results = explorer.search(
        "o", node_types=frozenset({NodeType.VENUE}), limit=10
    )["results"]

    assert {result["node_type"] for result in results} == {"venue"}
    assert len(explorer.search("o", node_types=None, limit=10)["results"]) > len(results)


def test_neighbourhood_explains_every_node_it_returns(explorer: Explorer) -> None:
    payload = explorer.neighbourhood(
        "event:e1",
        hops=2,
        direction=Direction.BOTH,
        max_degree=1000,
        through_classifications=False,
        min_confidence=0.0,
        limit=100,
    )

    assert payload["root"]["node_id"] == "event:e1"
    assert payload["truncated"] == 0
    for node in payload["nodes"]:
        if node["hops"]:
            assert node["explain"], f"{node['node_id']} arrived with no explanation"


def test_neighbourhood_stops_at_classification_nodes_by_default(
    explorer: Explorer,
) -> None:
    """A genre is reached but never expanded through, so it brings no strangers with it."""
    guarded = explorer.neighbourhood(
        "event:e1",
        hops=2,
        direction=Direction.BOTH,
        max_degree=1000,
        through_classifications=False,
        min_confidence=0.0,
        limit=100,
    )

    assert "genre:g1" in {node["node_id"] for node in guarded["nodes"]}


def test_neighbourhood_marks_the_edges_that_justify_each_hop(explorer: Explorer) -> None:
    payload = explorer.neighbourhood(
        "event:e1",
        hops=1,
        direction=Direction.BOTH,
        max_degree=1000,
        through_classifications=False,
        min_confidence=0.0,
        limit=100,
    )

    assert any(edge["on_path"] for edge in payload["edges"])
    assert all(
        edge["source"] in {node["node_id"] for node in payload["nodes"]}
        for edge in payload["edges"]
    )


def test_neighbourhood_truncation_keeps_the_nearest_nodes(explorer: Explorer) -> None:
    payload = explorer.neighbourhood(
        "event:e1",
        hops=2,
        direction=Direction.BOTH,
        max_degree=1000,
        through_classifications=False,
        min_confidence=0.0,
        limit=2,
    )

    assert len(payload["nodes"]) == 2
    assert payload["truncated"] > 0
    assert [node["hops"] for node in payload["nodes"]] == [0, 1]


def test_neighbourhood_rejects_an_unknown_node(explorer: Explorer) -> None:
    from event_graph.graph import NodeNotFoundError

    with pytest.raises(NodeNotFoundError):
        explorer.neighbourhood(
            "event:nope",
            hops=1,
            direction=Direction.BOTH,
            max_degree=1000,
            through_classifications=False,
            min_confidence=0.0,
            limit=10,
        )


def test_a_saved_slice_round_trips(
    source: InMemoryGraphSource, tmp_path: Path
) -> None:
    graph, metadata, report = GraphBuilder(source).build()
    path = tmp_path / "slice.pkl"

    save_graph(path, graph, metadata, report)
    saved = load_graph(path)

    assert saved.graph.node_count == graph.node_count
    assert saved.graph.edge_count == graph.edge_count
    assert saved.metadata.source == metadata.source
    assert saved.report.edges_dangling == report.edges_dangling
    assert "slice.pkl" in saved.describe()


def test_loading_a_missing_slice_says_how_to_build_one(tmp_path: Path) -> None:
    from event_graph.graph import ConfigurationError

    with pytest.raises(ConfigurationError, match="graph build"):
        load_graph(tmp_path / "absent.pkl")


def test_loading_an_older_save_format_is_refused(tmp_path: Path) -> None:
    import pickle

    from event_graph.graph import ConfigurationError

    path = tmp_path / "stale.pkl"
    metadata = BuildMetadata(
        source="fixture", slice=GraphSlice(), built_at=datetime.now(UTC), git_commit=None
    )
    with path.open("wb") as handle:
        pickle.dump(
            {"format_version": 0, "graph": EventGraph(), "metadata": metadata, "report": None},
            handle,
        )

    with pytest.raises(ConfigurationError, match="save format"):
        load_graph(path)
