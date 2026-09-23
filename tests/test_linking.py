"""Lineup linking must choose, explain and calibrate with no tunnel and no credentials."""

from event_graph.graph import GraphBuilder, InMemoryGraphSource, NodeType, Predicate, SourceClass
from event_graph.graph.graph import EventGraph
from event_graph.linking.evaluate import evaluate
from event_graph.linking.guards import is_placeholder, tribute_token
from event_graph.linking.linker import Arm, Linker
from event_graph.linking.mentions import Candidate, LineupMention
from event_graph.linking.proposals import propose
from event_graph.linking.ranker import GraphRanker

from tests.conftest import make_edge, make_node


def graph() -> EventGraph:
    """Two rows called "Nona": one has played this venue before, the other is more linked."""
    nodes = [
        make_node(NodeType.EVENT, "new", "Nona live"),
        make_node(NodeType.EVENT, "old", "Nona"),
        make_node(NodeType.EVENT, "far1", "Nona"),
        make_node(NodeType.EVENT, "far2", "Nona"),
        make_node(NodeType.VENUE, "v1", "Paradiso"),
        make_node(NodeType.VENUE, "v2", "Elsewhere"),
        make_node(NodeType.ARTIST, "local", "Nona"),
        make_node(NodeType.ARTIST, "popular", "Nona"),
    ]
    edges = [
        make_edge("event:new", Predicate.HELD_AT, "venue:v1"),
        make_edge("event:old", Predicate.HELD_AT, "venue:v1"),
        make_edge("event:far1", Predicate.HELD_AT, "venue:v2"),
        make_edge("event:far2", Predicate.HELD_AT, "venue:v2"),
        make_edge("artist:local", Predicate.PERFORMS_AT, "event:old"),
        make_edge("artist:popular", Predicate.PERFORMS_AT, "event:far1"),
        make_edge("artist:popular", Predicate.PERFORMS_AT, "event:far2"),
    ]
    built, _, _ = GraphBuilder(InMemoryGraphSource(nodes, edges)).build()
    return built


def mention(
    name: str = "Nona",
    *,
    title: str = "Nona live",
    linked: dict[str, str] | None = None,
    upcoming: bool = True,
    candidates: tuple[Candidate, ...] | None = None,
) -> LineupMention:
    return LineupMention(
        event_node_id="event:new",
        external_event_id="x1",
        provider_id="p1",
        name=name,
        title=title,
        country="NL",
        is_upcoming=upcoming,
        linked_artists=linked or {},
        candidates=candidates
        if candidates is not None
        else (
            Candidate(artist_node_id="artist:local", label="Nona", linked_events=1),
            Candidate(artist_node_id="artist:popular", label="Nona", linked_events=2),
        ),
    )


class TestGuards:
    def test_tribute_token_fires_on_title(self) -> None:
        assert tribute_token("Queen", "A Night of Queen") == "a night of"

    def test_performer_before_the_token_is_not_the_tribute_subject(self) -> None:
        title = "Niels Geusebroek sings Coldplay"
        assert tribute_token("Niels Geusebroek", title) is None
        assert tribute_token("Coldplay", title) == "sings"

    def test_noun_token_counts_against_the_name_before_it(self) -> None:
        assert tribute_token("Queen", "Queen Tribute Night") == "tribute"

    def test_by_marks_a_cover_act_only_inside_the_name(self) -> None:
        assert tribute_token("Bee Gees Forever (by MainCourse)", "Bee Gees by MainCourse") == "by"
        assert tribute_token("Amelie Lens", "Audio Obscura x EXHALE by Amelie Lens") is None

    def test_tribute_token_is_whole_word(self) -> None:
        assert tribute_token("Coverdale", "Coverdale live") is None

    def test_placeholder(self) -> None:
        assert is_placeholder(" TBA ")


class TestLinker:
    def test_graph_beats_popularity(self) -> None:
        decision = next(Linker(GraphRanker(graph())).decide_all([mention()]))
        assert decision.arm is Arm.GRAPH_RANKED
        assert decision.chosen is not None
        assert decision.chosen.candidate.artist_node_id == "artist:local"
        assert decision.chosen.features.reasons() == {"venue_history": 1}
        assert decision.popularity_choice() == decision.ranked[1].candidate

    def test_an_existing_link_does_not_vote_for_itself(self) -> None:
        g = graph()
        g.add_edge(make_edge("artist:popular", Predicate.PERFORMS_AT, "event:new"))
        linked = mention(linked={"artist:popular": "web"})
        decision = next(Linker(GraphRanker(g)).decide_all([linked]))
        popular = next(s for s in decision.ranked if s.candidate.artist_node_id == "artist:popular")
        assert popular.features.venue_history == 0
        assert popular.popularity == 1

    def test_placeholder_is_blocked(self) -> None:
        decision = next(Linker(GraphRanker(graph())).decide_all([mention("TBA")]))
        assert decision.chosen is None and decision.blocked == "placeholder name"


class TestProposals:
    def test_confidence_is_measured_precision_and_floor_applies(self) -> None:
        unique = (Candidate(artist_node_id="artist:local", label="Nona", linked_events=1),)
        labelled = [
            mention(linked={"artist:local": "web"}, upcoming=False, candidates=unique)
            for _ in range(40)
        ]
        target = mention(candidates=unique)
        tribute = mention(title="Nona tribute night", candidates=unique)

        decisions = list(Linker(GraphRanker(graph())).decide_all([*labelled, target, tribute]))
        evaluation = evaluate(decisions)
        proposals, dropped = propose(decisions, evaluation, run_id="r1", eval_command="test")

        assert len(proposals) == 1
        edge = proposals[0].edge
        assert edge.source_class is SourceClass.GRAPH_INFERRED
        assert edge.confidence == 1.0 and edge.run_id == "r1"
        assert edge.evidence is not None and edge.evidence["arm"] == "unique_name"
        assert dropped == {"unique_name + tribute: too few labelled cases to calibrate on": 1}


class TestSpelling:
    def test_accents_case_and_qualifiers_are_not_a_difference(self) -> None:
        from event_graph.linking.fuzzy import MatchKind, match

        found = match("iñigo quintero", "Íñigo Quintero")
        assert found is not None and found.kind is MatchKind.EQUAL
        found = match("James Hype (UK)", "James HYPE")
        assert found is not None and found.kind is MatchKind.EQUAL

    def test_a_short_single_word_never_matches(self) -> None:
        from event_graph.linking.fuzzy import match

        assert match("Luna (1)", "Luna") is None

    def test_a_numbered_name_is_another_act_of_that_name(self) -> None:
        from event_graph.linking.fuzzy import match

        assert match("Dimitri (1)", "Dimitri") is None

    def test_a_specific_name_inside_a_longer_one_is_contained(self) -> None:
        from event_graph.linking.fuzzy import MatchKind, match

        found = match("Concierto Ruth Lorenzo", "Ruth Lorenzo")
        assert found is not None and found.kind is MatchKind.CONTAINS
        assert match("Estiva festival", "Estiva") is None
