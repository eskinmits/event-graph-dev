"""The projection must be buildable and traversable with no tunnel and no credentials."""

import pytest

from event_graph.graph import (
    ENTITY_NODE_TYPES,
    ClickHouseGraphSource,
    ConfigurationError,
    Direction,
    EmptyGraphError,
    GraphBuilder,
    GraphSlice,
    InMemoryGraphSource,
    Node,
    NodeNotFoundError,
    NodeType,
    OntologyViolation,
    Predicate,
    SourceClass,
    UnknownNodeError,
    junk_hub_filter,
    keep_everything,
)
from event_graph.graph.edges import Edge
from event_graph.graph.graph import EventGraph
from event_graph.graph.inferred import inferred_table_ddl
from event_graph.graph.ontology import deterministic_edge_id, parse_node_id

from tests.conftest import make_edge, make_node


class TestOntology:
    def test_node_id_round_trips(self) -> None:
        assert parse_node_id("artist:abc-123") == (NodeType.ARTIST, "abc-123")

    def test_natural_key_may_contain_the_separator(self) -> None:
        assert parse_node_id("event:a:b") == (NodeType.EVENT, "a:b")

    @pytest.mark.parametrize("node_id", ["abc-123", "artist:", "wizard:abc"])
    def test_unusable_node_id_is_rejected(self, node_id: str) -> None:
        with pytest.raises(OntologyViolation):
            parse_node_id(node_id)

    def test_node_id_must_agree_with_node_type(self) -> None:
        with pytest.raises(OntologyViolation):
            Node(node_id="venue:v1", node_type=NodeType.ARTIST, natural_key="v1", label="x")

    def test_edge_id_is_deterministic(self) -> None:
        args = ("artist:a1", Predicate.PERFORMS_AT, "event:e1", "employee")
        assert deterministic_edge_id(*args) == deterministic_edge_id(*args)

    def test_edge_id_separates_sources(self) -> None:
        """The same link from two writers is two rows, never one overwriting the other."""
        head = ("artist:a1", Predicate.PERFORMS_AT, "event:e1")
        assert deterministic_edge_id(*head, "employee") != deterministic_edge_id(
            *head, "graph_inferred"
        )


class TestEdge:
    def test_evidence_json_is_parsed(self) -> None:
        edge = Edge.from_row(
            (
                "id",
                "artist:a1",
                "performs_at",
                "event:e1",
                "employee",
                "admin_verified",
                1.0,
                None,
                "",
                '{"rule": "title_match"}',
            )
        )
        assert edge.evidence == {"rule": "title_match"}
        assert edge.run_id is None
        assert not edge.is_inferred

    def test_confidence_outside_the_unit_interval_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            make_edge("artist:a1", Predicate.PERFORMS_AT, "event:e1", confidence=1.4)

    def test_inferred_edge_carries_its_run_and_evidence(self) -> None:
        edge = Edge.inferred(
            "artist:a1",
            Predicate.PERFORMS_AT,
            "event:e1",
            confidence=0.72,
            run_id="run-42",
            evidence={"path": ["venue:v1"], "rule": "venue_history"},
        )
        assert edge.is_inferred
        assert edge.source_class is SourceClass.GRAPH_INFERRED
        assert edge.run_id == "run-42"
        assert edge.evidence is not None


class TestBuild:
    def test_builds_and_counts(self, source: InMemoryGraphSource) -> None:
        graph, metadata, report = GraphBuilder(source).build()

        assert report.nodes_loaded == 7  # the junk hub is filtered out by default
        assert report.nodes_filtered == 1
        assert graph.node_count == 7
        assert metadata.source == "fixture"

    def test_dangling_edges_are_counted_not_fatal(self, source: InMemoryGraphSource) -> None:
        _, _, report = GraphBuilder(source).build()

        assert report.edges_dangling == 3  # one ghost artist, two from the filtered hub
        assert report.edges_loaded == 7

    def test_junk_hub_is_kept_when_asked(self, source: InMemoryGraphSource) -> None:
        graph, _, report = GraphBuilder(source, node_filter=keep_everything).build()

        assert "artist:junk" in graph
        assert report.nodes_filtered == 0
        assert report.edges_dangling == 1

    def test_off_ontology_edge_fails_a_strict_build(self) -> None:
        source = InMemoryGraphSource(
            [make_node(NodeType.VENUE, "v1", "Ziggo"), make_node(NodeType.GENRE, "g1", "Techno")],
            [make_edge("genre:g1", Predicate.HELD_AT, "venue:v1")],
        )
        with pytest.raises(OntologyViolation):
            GraphBuilder(source).build()

    def test_off_ontology_edge_is_counted_when_lenient(self) -> None:
        source = InMemoryGraphSource(
            [make_node(NodeType.VENUE, "v1", "Ziggo"), make_node(NodeType.GENRE, "g1", "Techno")],
            [make_edge("genre:g1", Predicate.HELD_AT, "venue:v1")],
        )
        _, _, report = GraphBuilder(source, strict=False).build()

        assert report.edges_off_ontology == 1
        assert report.edges_loaded == 0

    def test_empty_source_says_so(self) -> None:
        with pytest.raises(EmptyGraphError, match="tunnel"):
            GraphBuilder(InMemoryGraphSource([], [])).build()

    def test_report_summary_names_what_was_dropped(self, source: InMemoryGraphSource) -> None:
        _, _, report = GraphBuilder(source).build()

        assert "dangling edges" in report.summary()


class TestTraversal:
    @pytest.fixture
    def graph(self, source: InMemoryGraphSource) -> EventGraph:
        built, _, _ = GraphBuilder(source).build()
        return built

    def test_expand_reaches_across_hops(self, graph: EventGraph) -> None:
        neighbourhood = graph.expand("event:e1", hops=2)
        reached = {r.node.node_id: r.hops for r in neighbourhood}

        assert reached["artist:a1"] == 1
        assert reached["venue:v1"] == 1
        assert reached["city:c1"] == 2
        assert reached["artist:a2"] == 2

    def test_expand_respects_the_hop_limit(self, graph: EventGraph) -> None:
        assert "city:c1" not in {r.node.node_id for r in graph.expand("event:e1", hops=1)}

    def test_every_reached_node_can_explain_itself(self, graph: EventGraph) -> None:
        """A proposal nobody can explain is not one we would show ops."""
        for reached in graph.expand("event:e1", hops=2):
            if reached.hops:
                assert reached.path
                assert reached.explain()

    def test_path_confidence_is_the_weakest_link(self, graph: EventGraph) -> None:
        genre = next(
            r for r in graph.expand("event:e1", hops=2) if r.node.node_id == "genre:g1"
        )
        assert genre.confidence == pytest.approx(0.5)

    def test_confidence_floor_prunes_weak_edges(self, graph: EventGraph) -> None:
        reached = {r.node.node_id for r in graph.expand("event:e1", hops=2, min_confidence=0.7)}

        assert "genre:g1" not in reached
        assert "artist:a2" in reached

    def test_predicate_filter_restricts_the_walk(self, graph: EventGraph) -> None:
        reached = {
            r.node.node_id
            for r in graph.expand(
                "event:e1", hops=3, predicates=frozenset({Predicate.HELD_AT, Predicate.IN_CITY})
            )
        }

        # e2 is reached by walking held_at backwards out of the venue -- that is the
        # venue-history signal, not a leak
        assert reached == {"event:e1", "venue:v1", "city:c1", "event:e2"}

    def test_direction_is_respected(self, graph: EventGraph) -> None:
        out_only = {r.node.node_id for r in graph.expand("event:e1", hops=1, direction=Direction.OUT)}

        assert "venue:v1" in out_only
        assert "artist:a1" not in out_only  # performs_at points at the event

    def test_max_degree_stops_expansion_through_a_hub(self, graph: EventGraph) -> None:
        """The venue is reached, but the walk does not continue through it."""
        reached = {r.node.node_id for r in graph.expand("event:e1", hops=3, max_degree=2)}

        assert "venue:v1" in reached
        assert "city:c1" not in reached

    def test_walk_stops_at_classification_nodes(self, graph: EventGraph) -> None:
        """Every techno artist is two hops from every techno event; that is not a signal."""
        through_all = {r.node.node_id for r in graph.expand("event:e1", hops=2)}
        entities_only = {
            r.node.node_id
            for r in graph.expand("event:e1", hops=2, through=ENTITY_NODE_TYPES)
        }

        assert "genre:g1" in entities_only  # reached, just not expanded through
        assert through_all >= entities_only

    def test_unknown_node_is_a_readable_error(self, graph: EventGraph) -> None:
        with pytest.raises(NodeNotFoundError, match="nowhere"):
            graph.expand("artist:nowhere")

    def test_components_cluster_over_one_predicate(self, graph: EventGraph) -> None:
        clusters = graph.components(frozenset({Predicate.SAME_AS}))

        assert clusters == [frozenset({"event:e1", "event:e2"})]

    def test_components_ignore_untouched_nodes(self, graph: EventGraph) -> None:
        assert graph.components(frozenset({Predicate.OWNED_BY})) == []

    def test_prune_hubs_removes_by_degree(self, graph: EventGraph) -> None:
        pruned = graph.prune_hubs(max_degree=1, node_types=frozenset({NodeType.VENUE}))

        assert pruned == ["venue:v1"]
        assert "venue:v1" not in graph

    def test_adding_an_edge_to_a_missing_node_raises(self, graph: EventGraph) -> None:
        with pytest.raises(UnknownNodeError):
            graph.add_edge(make_edge("artist:nope", Predicate.PERFORMS_AT, "event:e1"))


class TestJunkHubFilter:
    def test_entity_hubs_are_dropped(self) -> None:
        keep = junk_hub_filter()
        assert not keep(make_node(NodeType.ARTIST, "x", "Unbekannt"))
        assert not keep(make_node(NodeType.VENUE, "y", "auditorium"))

    def test_real_events_named_after_a_hub_survive(self) -> None:
        """278 events really are called "Saturday Night"; dropping them deletes real rows."""
        assert junk_hub_filter()(make_node(NodeType.EVENT, "z", "Saturday Night"))


class TestClickHouseSourceSql:
    """Query shape only -- these run with no tunnel and no credentials."""

    def test_unsliced_query_is_a_plain_scan(self) -> None:
        source = ClickHouseGraphSource(GraphSlice(), client=object())  # type: ignore[arg-type]
        sql, parameters = source._edges_query()

        assert "WITH" not in sql
        assert parameters == {}

    def test_node_id_column_is_aliased(self) -> None:
        source = ClickHouseGraphSource(GraphSlice(), client=object())  # type: ignore[arg-type]
        sql, _ = source._nodes_query()

        assert "pk_dim_graph_nodes AS node_id" in sql

    def test_country_slice_walks_the_requested_number_of_hops(self) -> None:
        source = ClickHouseGraphSource(
            GraphSlice(country="ES", closure_hops=2),
            client=object(),  # type: ignore[arg-type]
        )
        sql, parameters = source._edges_query()

        assert "closure_edges_2" in sql
        assert parameters["country"] == "ES"

    def test_enum_parameters_are_plain_strings(self) -> None:
        source = ClickHouseGraphSource(
            GraphSlice(predicates=frozenset({Predicate.PERFORMS_AT})),
            client=object(),  # type: ignore[arg-type]
        )
        _, parameters = source._edges_query()

        assert parameters["predicates"] == ["performs_at"]
        assert all(type(value) is str for value in parameters["predicates"])

    def test_a_bad_table_name_is_caught_before_it_reaches_clickhouse(self) -> None:
        from event_graph.config import GraphTablesConfig
        from event_graph.graph import ConfigurationError

        source = ClickHouseGraphSource(
            GraphSlice(),
            client=object(),  # type: ignore[arg-type]
            tables=GraphTablesConfig(nodes_table="nodes; DROP TABLE x"),
        )
        with pytest.raises(ConfigurationError, match="identifier"):
            source._nodes_query()


class TestInferredTable:
    def test_ddl_matches_the_write_contract(self) -> None:
        ddl = inferred_table_ddl("machine_learning.graph_edges_inferred")
        assert "create table if not exists machine_learning.graph_edges_inferred" in ddl
        assert "ReplacingMergeTree(data_updated_at)" in ddl
        assert "order by (edge_id, run_id)" in ddl
        assert "evidence        String default '{}'" in ddl

    def test_ddl_columns_cover_every_edge_field(self) -> None:
        ddl = inferred_table_ddl("graph_edges_inferred")
        for column in Edge.COLUMNS:
            assert f"\n    {column} " in ddl

    def test_unsafe_table_name_is_rejected(self) -> None:
        with pytest.raises(ConfigurationError):
            inferred_table_ddl("x; drop table y")
