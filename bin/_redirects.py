"""Shared loading for the redirect-closure measurements.

`same_as` is rebuilt from `dbt.stg_backend__event_redirected` rather than from
`dbt_dev.bridge_graph_edges_source`, so these scripts keep working while the graph
tables are unavailable. The staging table is what dbt derives the `event_redirect` edges
from, so the closure is the same one `EventGraph.components({Predicate.SAME_AS})` produces.
"""

from dataclasses import dataclass
from typing import Final

import rustworkx as rx
from clickhouse_connect.driver.client import Client
from pydantic import BaseModel, ConfigDict

REDIRECTS_TABLE: Final = "dbt.stg_backend__event_redirected"
EVENTS_TABLE: Final = "dbt.dim_events"
ARTIST_BRIDGE_TABLE: Final = "dbt.bridge_event_artists"

# deletion placeholders and empty titles that redirects drain into. one of these fuses
# every cluster it touches, exactly like the artist and venue junk hubs in filters.py
SINK_TITLES: Final[frozenset[str]] = frozenset(
    {
        "deleted",
        "-",
        "",
        "test",
        "test event",
        "** this event has been deleted **",
    }
)


class EventFacts(BaseModel):
    """The `dim_events` columns the transfer-coverage funnel needs, for one event."""

    model_config = ConfigDict(frozen=True)

    event_id: str
    title: str
    has_artist: bool
    has_genre: bool
    has_provider: bool
    is_upcoming: bool
    is_redirected: bool
    country: str

    @property
    def is_live(self) -> bool:
        """A redirected row is the dead side of a merge -- nobody reads it again."""
        return not self.is_redirected


@dataclass(frozen=True, slots=True)
class RedirectPairs:
    """The raw redirect log, before closure."""

    pairs: tuple[tuple[str, str], ...]

    @property
    def self_loops(self) -> int:
        return sum(1 for src, dst in self.pairs if src == dst)

    @property
    def chained(self) -> int:
        """Pairs whose target is itself redirected -- why one hop under-reaches."""
        sources = {src for src, _ in self.pairs}
        return sum(1 for _, dst in self.pairs if dst in sources)

    def components(self, exclude: frozenset[str] = frozenset()) -> list[frozenset[str]]:
        """Transitively-closed clusters. The unit of work is the component, not the pair.

        `exclude` drops nodes *before* the closure runs, so a component that was only
        held together by a sink splits into the unrelated clusters it had fused.
        """
        graph: rx.PyGraph[str, None] = rx.PyGraph(multigraph=False)
        index: dict[str, int] = {}

        def node(event_id: str) -> int:
            existing = index.get(event_id)
            if existing is None:
                existing = graph.add_node(event_id)
                index[event_id] = existing
            return existing

        for src, dst in self.pairs:
            if src != dst and src not in exclude and dst not in exclude:
                graph.add_edge(node(src), node(dst), None)

        return [
            frozenset(graph[member] for member in component)
            for component in rx.connected_components(graph)
        ]


def load_redirect_pairs(client: Client) -> RedirectPairs:
    rows = client.query(
        f"SELECT event_id, destination_event_id FROM {REDIRECTS_TABLE}"
        " WHERE event_id IS NOT NULL AND destination_event_id IS NOT NULL"
    ).result_rows
    return RedirectPairs(tuple((str(src), str(dst)) for src, dst in rows))


def load_event_facts(client: Client) -> dict[str, EventFacts]:
    """Facts for every event named by the redirect log, keyed by event id."""
    rows = client.query(
        f"""
        SELECT event_id,
               event_title,
               event_has_artists,
               length(coalesce(event_genre_list, '')) > 0,
               length(event_list_of_ticket_providers) > 0,
               event_start_at > now(),
               coalesce(event_is_redirected, 0),
               event_country_iso
        FROM {EVENTS_TABLE}
        WHERE event_id IN (
            SELECT event_id FROM {REDIRECTS_TABLE} WHERE event_id IS NOT NULL
            UNION DISTINCT
            SELECT destination_event_id FROM {REDIRECTS_TABLE}
            WHERE destination_event_id IS NOT NULL
        )
        """
    ).result_rows
    facts = (
        EventFacts(
            event_id=str(row[0]),
            title=str(row[1]),
            has_artist=bool(row[2]),
            has_genre=bool(row[3]),
            has_provider=bool(row[4]),
            is_upcoming=bool(row[5]),
            is_redirected=bool(row[6]),
            country=str(row[7]),
        )
        for row in rows
    )
    return {fact.event_id: fact for fact in facts}


def load_artist_sets(client: Client) -> dict[str, frozenset[int]]:
    """Artist ids per event, for deciding whether donors in a component conflict."""
    rows = client.query(
        f"""
        SELECT e.event_id, groupUniqArray(b.fk_dim_artists)
        FROM {EVENTS_TABLE} AS e
        INNER JOIN {ARTIST_BRIDGE_TABLE} AS b ON b.fk_dim_events = e.pk_dim_events
        WHERE e.event_id IN (
            SELECT event_id FROM {REDIRECTS_TABLE} WHERE event_id IS NOT NULL
            UNION DISTINCT
            SELECT destination_event_id FROM {REDIRECTS_TABLE}
            WHERE destination_event_id IS NOT NULL
        )
        GROUP BY e.event_id
        """
    ).result_rows
    return {str(event_id): frozenset(artists) for event_id, artists in rows}


def is_sink(fact: EventFacts) -> bool:
    return fact.title.casefold().strip() in SINK_TITLES


def sink_event_ids(facts: dict[str, EventFacts]) -> frozenset[str]:
    """Events whose title is a deletion placeholder, to exclude before closure."""
    return frozenset(
        event_id for event_id, fact in facts.items() if is_sink(fact)
    )
