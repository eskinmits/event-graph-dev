"""Artist names Event Engine providers supplied, and the artist rows each name could mean.

The provider payload `[{name, spotifyUrl}]` sits on the raw `website` replica only -- the dbt
staging model drops it -- and on 27% of artist-less upcoming events it names a lineup that
was never linked. Resolving a name is where the work is: an exact unique match is a lookup,
and a name shared by several artist rows is the case graph structure has to decide.
"""

from collections.abc import Sequence
from typing import Any, Final

from clickhouse_connect.driver.client import Client
from pydantic import BaseModel, ConfigDict

from event_graph.config import GraphTablesConfig
from event_graph.graph.source import _identifier

EXTERNAL_EVENTS_TABLE: Final = "website.event_engine_external_event"
ARTISTS_TABLE: Final = "dbt.dim_artists"
# dim_artists carries no redirect or deleted flag, so liveness comes from staging
ARTIST_STAGING_TABLE: Final = "dbt_dev.stg_website__artist"


class Candidate(BaseModel):
    """One live artist row whose case-folded name equals the provider's name."""

    model_config = ConfigDict(frozen=True)

    artist_node_id: str
    label: str
    linked_events: int


class LineupMention(BaseModel):
    """One provider-supplied artist name on one internal event."""

    model_config = ConfigDict(frozen=True)

    event_node_id: str
    external_event_id: str
    provider_id: str
    name: str
    title: str
    country: str
    is_upcoming: bool
    # artist node id -> the raw `source` of the link, for splitting the eval by provenance
    linked_artists: dict[str, str]
    candidates: tuple[Candidate, ...]

    @property
    def is_artistless(self) -> bool:
        return not self.linked_artists

    @property
    def is_ambiguous(self) -> bool:
        return len(self.candidates) > 1

    def truth(self) -> Candidate | None:
        """The one candidate already linked to the event, when exactly one is."""
        linked = [c for c in self.candidates if c.artist_node_id in self.linked_artists]
        return linked[0] if len(linked) == 1 else None


def _mentions_query(tables: GraphTablesConfig) -> str:
    edges = _identifier(tables.edges_table, "edges_table")
    events = _identifier(tables.events_table, "events_table")
    return f"""
WITH
live_artists AS (
    SELECT concat('artist:', a.artist_id) AS node_id, a.artist_name AS label,
           lower(trim(a.artist_name)) AS name
    FROM {ARTISTS_TABLE} AS a
    INNER JOIN {ARTIST_STAGING_TABLE} AS s ON s.artist_id = a.artist_id
    WHERE s.artist_redirected_to_artist_id IS NULL
      AND coalesce(s.artist_is_deleted, false) = false
),
links AS (
    SELECT src_node_id AS artist_node_id, dst_node_id AS event_node_id, source
    FROM {edges} WHERE predicate = 'performs_at'
),
market_events AS (
    SELECT event_id, event_title, event_country_iso, event_start_at > now() AS is_upcoming
    FROM {events}
    WHERE coalesce(event_is_redirected, 0) = 0 AND event_country_iso = {{country:String}}
),
external AS (
    SELECT x.id AS external_event_id, x.internal_event_id AS event_id,
           x.external_provider_id AS provider_id, x.artists AS artists
    FROM {EXTERNAL_EVENTS_TABLE} AS x FINAL
    WHERE x._peerdb_is_deleted = 0
      AND x.internal_event_id IN (SELECT event_id FROM market_events)
),
mentions AS (
    SELECT external_event_id, event_id, provider_id,
           trim(JSONExtractString(m, 'name')) AS raw_name, lower(raw_name) AS name
    FROM external
    ARRAY JOIN JSONExtractArrayRaw(artists) AS m
    WHERE raw_name != ''
),
link_counts AS (
    SELECT artist_node_id, count() AS n FROM links GROUP BY artist_node_id
),
candidates AS (
    SELECT la.name AS name,
           groupArray((la.node_id, la.label, toUInt64(coalesce(lc.n, 0)))) AS candidates
    FROM live_artists AS la
    LEFT JOIN link_counts AS lc ON lc.artist_node_id = la.node_id
    WHERE la.name IN (SELECT name FROM mentions)
    GROUP BY la.name
),
linked AS (
    SELECT event_node_id, groupArray((artist_node_id, source)) AS artists
    FROM links
    WHERE event_node_id IN (SELECT concat('event:', event_id) FROM market_events)
    GROUP BY event_node_id
)
SELECT concat('event:', m.event_id), m.external_event_id, m.provider_id, m.raw_name,
       e.event_title, e.event_country_iso, e.is_upcoming, l.artists, c.candidates
FROM mentions AS m
INNER JOIN market_events AS e ON e.event_id = m.event_id
LEFT JOIN linked AS l ON l.event_node_id = concat('event:', m.event_id)
LEFT JOIN candidates AS c ON c.name = m.name
"""


def _mention(row: Sequence[Any]) -> LineupMention:
    event, external, provider, name, title, country, upcoming, linked, candidates = row
    return LineupMention(
        event_node_id=event,
        external_event_id=external,
        provider_id=provider,
        name=name,
        title=title or "",
        country=country,
        is_upcoming=bool(upcoming),
        linked_artists={artist: source for artist, source in linked or ()},
        candidates=tuple(
            Candidate(artist_node_id=node_id, label=label, linked_events=int(n))
            for node_id, label, n in candidates or ()
        ),
    )


def load_mentions(client: Client, tables: GraphTablesConfig, country: str) -> list[LineupMention]:
    """Every provider artist name on the market's live events, past and upcoming.

    Past events are loaded on purpose: the ones already linked are the eval set.
    """
    rows = client.query(_mentions_query(tables), parameters={"country": country}).result_rows
    if not rows:
        raise LookupError(
            f"no Event Engine lineup names found for country {country!r}. "
            "Check the ISO-2 code, and that the tunnel points at the Transformations service."
        )
    return [_mention(row) for row in rows]
