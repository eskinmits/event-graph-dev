"""Choosing between artist rows that share a name, from what the graph knows about the event.

A name alone cannot tell two "Los Fabulosos Cadillacs" rows apart; the event's surroundings
can. Each signal is a walk of one or two hops from the candidate's other events back to
something this event also has -- its venue, its city, its promoter, its series, the other
acts on its bill. The event being scored is always excluded from the candidate's history,
so an existing link cannot vote for itself.
"""

from dataclasses import dataclass
from functools import cache
from typing import Final

from event_graph.graph.edges import Edge
from event_graph.graph.graph import Direction, EventGraph
from event_graph.graph.nodes import Node
from event_graph.graph.ontology import Predicate

# a signal counts up to this many supporting events; the tenth show at a venue says little
# the third did not
SIGNAL_CAP: Final = 3

WEIGHTS: Final[dict[str, int]] = {
    "venue_history": 3,
    "brand_history": 3,
    "series_history": 3,
    "cobilled_before": 3,
    "similar_to_cobilled": 2,
    "city_history": 1,
}


@dataclass(frozen=True, slots=True)
class EventContext:
    """What the event being linked has, for candidates' histories to be matched against."""

    event_node_id: str
    venues: frozenset[str]
    cities: frozenset[str]
    brands: frozenset[str]
    series: frozenset[str]
    cobilled: frozenset[str]


@dataclass(frozen=True, slots=True)
class Features:
    """Structural support for one candidate on one event, each a count of events or artists."""

    venue_history: int
    city_history: int
    brand_history: int
    series_history: int
    cobilled_before: int
    similar_to_cobilled: int

    @property
    def score(self) -> int:
        return sum(
            weight * min(getattr(self, name), SIGNAL_CAP) for name, weight in WEIGHTS.items()
        )

    def reasons(self) -> dict[str, int]:
        """The signals that fired, for `evidence`."""
        return {name: getattr(self, name) for name in WEIGHTS if getattr(self, name)}


type SupportPaths = dict[str, list[tuple[Edge, ...]]]


class GraphRanker:
    """Structural features over one built projection, with per-node lookups cached."""

    def __init__(self, graph: EventGraph) -> None:
        self.graph = graph
        # bound per instance, so each ranker caches against its own graph
        self._events_of = cache(self._events_of_uncached)
        self._out = cache(self._out_uncached)
        self._similar = cache(self._similar_uncached)

    def context(self, event_node_id: str, cobilled: frozenset[str]) -> EventContext:
        venues = self._out(event_node_id, Predicate.HELD_AT)
        return EventContext(
            event_node_id=event_node_id,
            venues=venues,
            cities=self._cities(venues),
            brands=self._out(event_node_id, Predicate.PROMOTED_BY),
            series=self._out(event_node_id, Predicate.SERIES_OF),
            cobilled=cobilled,
        )

    def features(self, context: EventContext, artist_node_id: str) -> Features:
        cobilled = context.cobilled - {artist_node_id}
        events = self._events_of(artist_node_id) - {context.event_node_id}
        cobilled_events = frozenset().union(*(self._events_of(a) for a in cobilled)) - {
            context.event_node_id
        }

        def sharing(predicate: Predicate, values: frozenset[str]) -> int:
            if not values:
                return 0
            return sum(1 for event in events if self._out(event, predicate) & values)

        city_history = 0
        if context.cities:
            city_history = sum(
                1 for event in events
                if self._cities(self._out(event, Predicate.HELD_AT)) & context.cities
            )

        return Features(
            venue_history=sharing(Predicate.HELD_AT, context.venues),
            city_history=city_history,
            brand_history=sharing(Predicate.PROMOTED_BY, context.brands),
            series_history=sharing(Predicate.SERIES_OF, context.series),
            cobilled_before=len(events & cobilled_events),
            similar_to_cobilled=len(self._similar(artist_node_id) & cobilled),
        )

    def support(
        self, context: EventContext, artist_node_id: str, *, per_signal: int = SIGNAL_CAP
    ) -> SupportPaths:
        """The edges behind each signal in `features`, for drawing why a candidate won.

        Each path runs from the candidate out to something the event also has, and back to
        the event, so it reads left to right as an argument. Capped per signal: the count
        in `features` is the full one, this is the part worth looking at.
        """
        target = context.event_node_id
        paths: SupportPaths = {}

        def add(signal: str, path: tuple[Edge, ...]) -> bool:
            found = paths.setdefault(signal, [])
            if len(found) < per_signal:
                found.append(path)
            return len(found) >= per_signal

        performs = {
            edge.dst_node_id: edge
            for edge in self._out_edges(artist_node_id, Predicate.PERFORMS_AT)
            if edge.dst_node_id != target
        }
        shared = (
            ("venue_history", Predicate.HELD_AT, context.venues),
            ("brand_history", Predicate.PROMOTED_BY, context.brands),
            ("series_history", Predicate.SERIES_OF, context.series),
        )
        for signal, predicate, values in shared:
            if not values:
                continue
            own = {e.dst_node_id: e for e in self._out_edges(target, predicate)}
            for event in sorted(performs):
                hit = next(
                    (e for e in self._out_edges(event, predicate) if e.dst_node_id in values), None
                )
                if hit and add(signal, (performs[event], hit, own[hit.dst_node_id])):
                    break

        if context.cities:
            own_venues = {e.dst_node_id: e for e in self._out_edges(target, Predicate.HELD_AT)}
            own_city: dict[str, tuple[Edge, Edge]] = {
                in_city.dst_node_id: (held_at, in_city)
                for held_at in own_venues.values()
                for in_city in self._out_edges(held_at.dst_node_id, Predicate.IN_CITY)
            }
            # gigs at other venues in the city say something the venue signal has not already
            elsewhere_first = sorted(
                performs,
                key=lambda e: (
                    any(v.dst_node_id in context.venues for v in self._out_edges(e, Predicate.HELD_AT)),
                    e,
                ),
            )
            for event in elsewhere_first:
                via_city = next(
                    (
                        (venue, city)
                        for venue in self._out_edges(event, Predicate.HELD_AT)
                        for city in self._out_edges(venue.dst_node_id, Predicate.IN_CITY)
                        if city.dst_node_id in own_city
                    ),
                    None,
                )
                if via_city:
                    their_venue, their_city = via_city
                    back_venue, back_city = own_city[their_city.dst_node_id]
                    path = (performs[event], their_venue, their_city, back_city, back_venue)
                    if add("city_history", path):
                        break

        for other in sorted(context.cobilled - {artist_node_id}):
            together = {
                e.dst_node_id: e for e in self._out_edges(other, Predicate.PERFORMS_AT)
            }
            for event in sorted(set(performs) & set(together)):
                if add("cobilled_before", (performs[event], together[event])):
                    break
            for edge, node in self._incident(artist_node_id, Predicate.SIMILAR_TO):
                if node.node_id == other:
                    add("similar_to_cobilled", (edge,))
                    break
        return {signal: found for signal, found in paths.items() if found}

    def _out_edges(self, node_id: str, predicate: Predicate) -> list[Edge]:
        if node_id not in self.graph:
            return []
        return [
            edge
            for edge, _ in self.graph.incident(
                node_id, predicates=frozenset({predicate}), direction=Direction.OUT
            )
        ]

    def _incident(self, node_id: str, predicate: Predicate) -> list[tuple[Edge, Node]]:
        if node_id not in self.graph:
            return []
        return list(
            self.graph.incident(
                node_id, predicates=frozenset({predicate}), direction=Direction.BOTH
            )
        )

    def _cities(self, venues: frozenset[str]) -> frozenset[str]:
        return frozenset().union(*(self._out(venue, Predicate.IN_CITY) for venue in venues))

    def _events_of_uncached(self, artist_node_id: str) -> frozenset[str]:
        return self._out_uncached(artist_node_id, Predicate.PERFORMS_AT)

    def _out_uncached(self, node_id: str, predicate: Predicate) -> frozenset[str]:
        if node_id not in self.graph:
            return frozenset()
        return frozenset(
            node.node_id
            for _, node in self.graph.incident(
                node_id, predicates=frozenset({predicate}), direction=Direction.OUT
            )
        )

    def _similar_uncached(self, artist_node_id: str) -> frozenset[str]:
        if artist_node_id not in self.graph:
            return frozenset()
        return frozenset(
            node.node_id
            for _, node in self.graph.incident(
                artist_node_id, predicates=frozenset({Predicate.SIMILAR_TO}), direction=Direction.BOTH
            )
        )
