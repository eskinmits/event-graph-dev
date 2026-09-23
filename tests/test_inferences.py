"""Inferred edges must be viewable, and explained, without ever entering the projection."""

from pathlib import Path

import pytest

from event_graph.graph import ConfigurationError, Direction, Edge, GraphBuilder, InMemoryGraphSource
from event_graph.graph.ontology import Predicate
from event_graph.web import Explorer
from event_graph.web.inferences import InferredOverlay

from tests.conftest import make_edge


@pytest.fixture
def proposal() -> Edge:
    """Amelie Lens proposed for Awakenings ADE, on the strength of being similar to its act."""
    return Edge.inferred(
        "artist:a2",
        Predicate.PERFORMS_AT,
        "event:e2",
        confidence=0.9,
        run_id="run-1",
        evidence={
            "arm": "unique_name",
            "provider_name": "Amelie Lens",
            "event_title": "Awakenings ADE",
            "artist_label": "Amelie Lens",
            "same_name_rows": 2,
            "graph_signals": {"similar_to_cobilled": 1},
            "cobilled": ["artist:a1"],
            "candidates": [
                {"artist_node_id": "artist:a2", "label": "Amelie Lens", "graph_score": 2},
                {"artist_node_id": "artist:elsewhere", "label": "Amelie Lens", "graph_score": 0},
            ],
            "rule_precision": 0.9,
        },
    )


@pytest.fixture
def explorer(source: InMemoryGraphSource, proposal: Edge) -> Explorer:
    graph, metadata, report = GraphBuilder(source).build()
    return Explorer(graph, metadata, report, InferredOverlay([proposal], "fixture"))


def test_overlay_round_trips_through_jsonl(tmp_path: Path, proposal: Edge) -> None:
    path = tmp_path / "proposals.jsonl"
    path.write_text(proposal.model_dump_json() + "\n")

    overlay = InferredOverlay.from_jsonl(path)

    assert overlay.edges == [proposal]
    assert overlay.summary()["rules"]["unique_name"]["proposals"] == 1


def test_missing_overlay_file_says_how_to_make_one(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="lineup_linking"):
        InferredOverlay.from_jsonl(tmp_path / "absent.jsonl")


def test_inferred_edges_never_enter_the_projection(explorer: Explorer, proposal: Edge) -> None:
    assert proposal.edge_id not in {edge.edge_id for edge in explorer.graph.edges()}


def test_worklist_filters_by_artist_or_event(explorer: Explorer) -> None:
    assert explorer.inferences("amelie", "", limit=10, offset=0)["matched"] == 1
    assert explorer.inferences("ade", "", limit=10, offset=0)["matched"] == 1
    assert explorer.inferences("coldplay", "", limit=10, offset=0)["matched"] == 0
    assert explorer.inferences("", "graph_ranked", limit=10, offset=0)["matched"] == 0


def test_neighbourhood_draws_the_proposal_and_its_far_end(
    explorer: Explorer, proposal: Edge
) -> None:
    payload = explorer.neighbourhood(
        "event:e2",
        hops=1,
        direction=Direction.BOTH,
        max_degree=1000,
        through_classifications=False,
        min_confidence=0.0,
        limit=300,
    )

    inferred = [edge for edge in payload["edges"] if edge["inferred"]]
    assert [edge["edge_id"] for edge in inferred] == [proposal.edge_id]
    assert "artist:a2" in {node["node_id"] for node in payload["nodes"]}


def test_reasoning_names_the_path_and_the_rival(explorer: Explorer, proposal: Edge) -> None:
    payload = explorer.inference(proposal.edge_id)

    roles = {node["node_id"]: node["role"] for node in payload["nodes"]}
    assert roles["event:e2"] == "target"
    assert roles["artist:a2"] == "proposed"
    assert roles["artist:elsewhere"] == "rival"
    assert list(payload["signals"]) == ["similar_to_cobilled"]
    rival = next(node for node in payload["nodes"] if node["node_id"] == "artist:elsewhere")
    assert rival["label"] == "Amelie Lens"


def test_prediction_hides_the_answer_and_still_finds_it(explorer: Explorer) -> None:
    """With her link to e1 hidden, her other gig at the same venue still leads the walk to her."""
    explorer.graph.add_edge(make_edge("artist:a1", Predicate.PERFORMS_AT, "event:e2"))
    payload = explorer.prediction("event:e1", hide_known=True, limit=10)

    assert payload["hidden"] == payload["known"] == 1
    ranked = {row["artist_node_id"]: row for row in payload["predictions"]}
    assert "artist:a1" in ranked and ranked["artist:a1"]["is_true"]
    assert payload["truth"][0]["rank"] == ranked["artist:a1"]["rank"]
    route = ranked["artist:a1"]["paths"][0]["nodes"]
    assert route[0] == "event:e1" and route[-1] == "artist:a1"
    # the hidden link itself is never the route
    assert route != ["event:e1", "artist:a1"]


def test_prediction_refuses_a_non_event(explorer: Explorer) -> None:
    with pytest.raises(ValueError, match="predictions are for events"):
        explorer.prediction("venue:v1", hide_known=True, limit=10)


def test_prediction_examples_report_their_own_hit_rates(explorer: Explorer) -> None:
    payload = explorer.prediction_examples(10)

    assert payload["sample"] == len(payload["rows"])
    assert 0.0 <= payload["recall_at_1"] <= payload["recall_at_10"] <= 1.0


def test_prediction_never_walks_a_duplicate_listing(explorer: Explorer) -> None:
    """e2 is a redirect of e1; its artists would be the answer, not a prediction."""
    explorer.graph.add_edge(make_edge("artist:a2", Predicate.PERFORMS_AT, "event:e2"))
    payload = explorer.prediction("event:e1", hide_known=True, limit=10)

    for row in payload["predictions"]:
        for path in row["paths"]:
            assert path["nodes"][:2] != ["event:e1", "event:e2"]
    assert all(edge["predicate"] != "same_as" for edge in payload["edges"])


def test_prediction_examples_carry_the_venue_baseline(explorer: Explorer) -> None:
    payload = explorer.prediction_examples(10)

    assert 0.0 <= payload["venue_recall_at_1"] <= payload["venue_recall_at_10"] <= 1.0
    assert all("venue_rank" in row for row in payload["rows"])
