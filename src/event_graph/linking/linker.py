"""From provider lineup names to one decision per name: which artist, by which rule, and why.

The eval and the proposals run the same decisions, so the precision reported for a rule is
the precision of exactly the code that writes its proposals.
"""

from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from event_graph.linking.fuzzy import Match, MatchKind, match
from event_graph.linking.guards import is_common_word, is_placeholder, tribute_token
from event_graph.linking.mentions import Candidate, LineupMention
from event_graph.linking.neighbourhood import Neighbour, Neighbourhood
from event_graph.linking.ranker import EventContext, Features, GraphRanker

# how many of the walk's nearest artists a name without an exact match is compared against
NEIGHBOURHOOD_LIMIT: Final = 300


class Arm(StrEnum):
    """The rule that chose an artist. Each is calibrated separately."""

    UNIQUE_NAME = "unique_name"
    GRAPH_RANKED = "graph_ranked"
    POPULARITY_ONLY = "popularity_only"
    NEIGHBOURHOOD_EQUAL = "neighbourhood_equal"
    NEIGHBOURHOOD_TYPO = "neighbourhood_typo"
    NEIGHBOURHOOD_CONTAINS = "neighbourhood_contains"


ARM_FOR_MATCH: Final[dict[MatchKind, Arm]] = {
    MatchKind.EQUAL: Arm.NEIGHBOURHOOD_EQUAL,
    MatchKind.TYPO: Arm.NEIGHBOURHOOD_TYPO,
    MatchKind.CONTAINS: Arm.NEIGHBOURHOOD_CONTAINS,
}


@dataclass(frozen=True, slots=True)
class NeighbourMatch:
    """A spelling match found among the artists near an event, and where it sat."""

    neighbour: Neighbour
    match: Match
    neighbourhood_size: int


@dataclass(frozen=True, slots=True)
class Scored:
    candidate: Candidate
    features: Features
    popularity: int


@dataclass(frozen=True, slots=True)
class Decision:
    """What the linker concluded about one mention."""

    mention: LineupMention
    arm: Arm | None
    ranked: tuple[Scored, ...]
    tribute: str | None
    blocked: str | None
    # the other acts resolved on the same bill, which co-billing signals were scored against
    cobilled: frozenset[str] = frozenset()
    # set when the artist was found by spelling among the event's graph neighbours
    matches: tuple[NeighbourMatch, ...] = ()

    @property
    def chosen(self) -> Scored | None:
        return None if self.blocked or not self.ranked else self.ranked[0]

    @property
    def runner_up(self) -> Scored | None:
        return self.ranked[1] if len(self.ranked) > 1 else None

    def popularity_choice(self) -> Candidate | None:
        """The baseline: whichever candidate has the most other linked events."""
        if not self.ranked:
            return None
        return max(self.ranked, key=lambda s: (s.popularity, s.candidate.artist_node_id)).candidate


def popularity(mention: LineupMention, candidate: Candidate) -> int:
    """Linked-event count, minus this event's own link so the answer cannot vote for itself."""
    return candidate.linked_events - (candidate.artist_node_id in mention.linked_artists)


class Linker:
    """Exact names first; names with no exact match are looked for near the event.

    Without a `neighbourhood` the second step is skipped, which is how the exact-name
    rules were measured on their own.
    """

    def __init__(self, ranker: GraphRanker, neighbourhood: Neighbourhood | None = None) -> None:
        self.ranker = ranker
        self.neighbourhood = neighbourhood

    def decide_all(self, mentions: Iterable[LineupMention]) -> Iterator[Decision]:
        by_event: dict[str, list[LineupMention]] = defaultdict(list)
        for mention in mentions:
            by_event[mention.event_node_id].append(mention)
        for event_mentions in by_event.values():
            yield from self.decide_event(event_mentions)

    def decide_event(self, mentions: list[LineupMention]) -> Iterator[Decision]:
        """Decide every mention on one event, each using the rest of the bill as context."""
        resolved = {
            id(m): m.candidates[0].artist_node_id
            for m in mentions
            if len(m.candidates) == 1 and not is_placeholder(m.name)
            and not tribute_token(m.name, m.title)
        }
        for mention in mentions:
            cobilled = frozenset(v for k, v in resolved.items() if k != id(mention))
            context = self.ranker.context(mention.event_node_id, cobilled)
            yield self.decide(mention, context)

    def decide(self, mention: LineupMention, context: EventContext) -> Decision:
        tribute = tribute_token(mention.name, mention.title)
        if is_placeholder(mention.name):
            return Decision(mention, None, (), tribute, "placeholder name", context.cobilled)
        if not mention.candidates:
            return self._decide_in_neighbourhood(mention, context, tribute)

        ranked = tuple(
            sorted(
                (
                    Scored(c, self.ranker.features(context, c.artist_node_id), popularity(mention, c))
                    for c in mention.candidates
                ),
                key=lambda s: (s.features.score, s.popularity, s.candidate.artist_node_id),
                reverse=True,
            )
        )
        if len(ranked) == 1:
            return Decision(mention, Arm.UNIQUE_NAME, ranked, tribute, None, context.cobilled)

        separated = ranked[0].features.score > ranked[1].features.score
        arm = Arm.GRAPH_RANKED if separated else Arm.POPULARITY_ONLY
        blocked = None
        if arm is Arm.POPULARITY_ONLY and is_common_word(len(ranked)):
            blocked = "common-word name with no structural support"
        return Decision(mention, arm, ranked, tribute, blocked, context.cobilled)

    def _decide_in_neighbourhood(
        self, mention: LineupMention, context: EventContext, tribute: str | None
    ) -> Decision:
        """No artist is called this; is one near the event spelled almost like it?"""
        if self.neighbourhood is None:
            return Decision(
                mention, None, (), tribute, "no artist row with this name", context.cobilled
            )
        # the event's own links are hidden from the walk, so they cannot vote for themselves
        neighbours = self.neighbourhood.artists(
            mention.event_node_id, frozenset(mention.linked_artists), NEIGHBOURHOOD_LIMIT
        )
        found = sorted(
            (
                NeighbourMatch(n, m, len(neighbours))
                for n in neighbours
                if not is_placeholder(n.label) and (m := match(mention.name, n.label))
            ),
            key=lambda f: (f.match.kind, f.match.similarity, f.neighbour.score),
            reverse=True,
        )
        if not found:
            return Decision(
                mention,
                None,
                (),
                tribute,
                "no artist row with this name, and none near the event spelled like it",
                context.cobilled,
            )

        ranked = tuple(
            Scored(
                candidate,
                self.ranker.features(context, candidate.artist_node_id),
                popularity(mention, candidate),
            )
            for candidate in (
                Candidate(
                    artist_node_id=f.neighbour.artist_node_id,
                    label=f.neighbour.label,
                    linked_events=f.neighbour.linked_events,
                )
                for f in found
            )
        )
        arm = ARM_FOR_MATCH[found[0].match.kind]
        return Decision(mention, arm, ranked, tribute, None, context.cobilled, tuple(found))
