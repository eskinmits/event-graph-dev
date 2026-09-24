"""Export the Spanish conflict worklist as a static snapshot for the Disco app.

Disco hosts static files only, so the app cannot hold the projection. This script does the
graph work here and writes one JSON file the page reads: every cluster of upcoming events
that share an artist on the same date, each with its one-hop neighbourhood from the cached
slice and a rule-based reading of what the conflict probably is.

The conflict rules mirror `bridge_graph_edges_derived` on the dbt branch (festivals and
placeholder artists excluded, ordinal sessions downgraded), applied to upcoming events with
at least one side in Spain. Rerun it to refresh the snapshot:

    uv run python disco/export_snapshot.py --load slices/es.pkl
"""

import hashlib
import logging
import subprocess
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

import click
from clickhouse_connect.driver.exceptions import OperationalError
from pydantic import BaseModel

from event_graph.clickhouse import get_client
from event_graph.config import get_config
from event_graph.graph.graph import EventGraph
from event_graph.graph.ontology import NodeType, Predicate
from event_graph.graph.store import load_graph

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT: Final = Path(__file__).parent / "ekg-use-cases" / "data" / "snapshot.json"

# mirrors junk_artist_labels in bridge_graph_edges_derived.sql
JUNK_ARTIST_LABELS: Final[tuple[str, ...]] = (
    "unbekannt",
    "auditorium",
    "saturday night",
    "tba",
    "tbc",
    "various artists",
)

# mirrors second_session_pattern in bridge_graph_edges_derived.sql
SECOND_SESSION_PATTERN: Final = (
    r"\b(primer|segundo|tercer|1er|2º|2o|3er|3º)\s+pase\b"
    r"|\b(primera|segunda|tercera)\s+(función|funcion|sesión|sesion)\b"
    r"|\b(early|late|second|2nd|matinee)\s+show\b"
)

EVENT_CONTEXT_PREDICATES: Final[frozenset[Predicate]] = frozenset(
    {
        Predicate.PERFORMS_AT,
        Predicate.HELD_AT,
        Predicate.HAS_GENRE,
        Predicate.IMPORTED_AS,
        Predicate.SAME_AS,
        Predicate.PROMOTED_BY,
        Predicate.SOLD_VIA,
        Predicate.SERIES_OF,
    }
)


class Reading(StrEnum):
    """What a conflict cluster most likely is, by rule. Ordered from most to least actionable."""

    DUPLICATE_LISTING = "duplicate_listing"
    DUPLICATE_VENUE = "duplicate_venue"
    WRONG_LINK = "wrong_link"
    TWO_SHOWS = "two_shows"


READING_ORDER: Final[dict[Reading, int]] = {reading: rank for rank, reading in enumerate(Reading)}


class ConflictPair(BaseModel):
    src: str
    dst: str
    event_date: str
    is_same_venue: bool
    is_possible_second_session: bool
    shared_artists: list[str]

    @property
    def confidence(self) -> float:
        if self.is_possible_second_session:
            return 0.3
        if self.is_same_venue:
            return 0.9
        if len(self.shared_artists) > 1:
            return 0.8
        return 0.6


class SnapshotEvent(BaseModel):
    node_id: str
    title: str
    starts_at: str
    venue_id: int
    venue_name: str
    city_name: str
    country_iso: str
    category: str
    admin_url: str
    frontend_url: str
    in_slice: bool


class SnapshotNode(BaseModel):
    id: str
    type: str
    label: str
    degree: int | None


class SnapshotEdge(BaseModel):
    source: str
    target: str
    predicate: str
    provenance: str
    source_class: str
    confidence: float


class SnapshotPair(BaseModel):
    src: str
    dst: str
    confidence: float
    is_same_venue: bool
    is_possible_second_session: bool
    shared_artists: list[str]


class SnapshotCluster(BaseModel):
    id: str
    event_date: str
    confidence: float
    reading: Reading
    artists: list[str]
    events: list[SnapshotEvent]
    pairs: list[SnapshotPair]
    nodes: list[SnapshotNode]
    edges: list[SnapshotEdge]


class Snapshot(BaseModel):
    exported_at: str
    git_commit: str
    slice: str
    rules: list[str]
    clusters: list[SnapshotCluster]


def fetch_conflict_pairs() -> list[ConflictPair]:
    tables = get_config().graph
    sql = f"""
        with appearances as (
            select
                edges.src_node_id as artist_node_id,
                edges.dst_node_id as event_node_id,
                toDate(events.event_start_at) as event_start_date,
                events.venue_id as venue_id,
                events.event_country_iso = 'ES' as is_es,
                match(lower(events.event_title), {{pattern:String}}) as is_numbered_session
            from {tables.edges_table} as edges
            inner join {tables.events_table} as events
                on edges.dst_node_id = concat('event:', events.{tables.events_key_column})
            inner join {tables.nodes_table} as nodes
                on edges.src_node_id = nodes.{tables.node_id_column}
            where edges.predicate = 'performs_at'
                and events.event_is_redirected = 0
                and events.event_start_at > now()
                and ifNull(events.event_category, '') != 'festivals'
                and not has({{junk:Array(String)}}, lower(trim(nodes.label)))
        )
        select
            least(first.event_node_id, second.event_node_id),
            greatest(first.event_node_id, second.event_node_id),
            toString(first.event_start_date),
            first.venue_id = second.venue_id,
            first.is_numbered_session or second.is_numbered_session,
            groupUniqArray(first.artist_node_id)
        from appearances as first
        inner join appearances as second
            on first.artist_node_id = second.artist_node_id
            and first.event_start_date = second.event_start_date
            and first.event_node_id < second.event_node_id
        where first.is_es or second.is_es
        group by 1, 2, 3, 4, 5
    """
    rows = get_client().query(
        sql, parameters={"pattern": SECOND_SESSION_PATTERN, "junk": list(JUNK_ARTIST_LABELS)}
    ).result_rows
    return [
        ConflictPair(
            src=src,
            dst=dst,
            event_date=event_date,
            is_same_venue=bool(same_venue),
            is_possible_second_session=bool(second_session),
            shared_artists=sorted(artists),
        )
        for src, dst, event_date, same_venue, second_session, artists in rows
    ]


def fetch_events(node_ids: Iterable[str], in_slice: set[str]) -> dict[str, SnapshotEvent]:
    tables = get_config().graph
    sql = f"""
        select
            concat('event:', {tables.events_key_column}),
            event_title,
            formatDateTime(toTimeZone(event_start_at, 'Europe/Madrid'), '%Y-%m-%d %H:%i'),
            venue_id,
            venue_name,
            city_name,
            event_country_iso,
            ifNull(event_category, ''),
            event_admin_url,
            event_frontend_url
        from {tables.events_table}
        where {tables.events_key_column} in {{ids:Array(String)}}
    """
    ids = [node_id.removeprefix("event:") for node_id in node_ids]
    rows = get_client().query(sql, parameters={"ids": ids}).result_rows
    return {
        row[0]: SnapshotEvent(
            node_id=row[0],
            title=row[1],
            starts_at=row[2],
            venue_id=row[3],
            venue_name=row[4],
            city_name=row[5],
            country_iso=row[6],
            category=row[7],
            admin_url=row[8],
            frontend_url=row[9],
            in_slice=row[0] in in_slice,
        )
        for row in rows
    }


def cluster_pairs(pairs: Sequence[ConflictPair]) -> list[list[ConflictPair]]:
    """Group pairs into connected components: four listings of one night are one task, not six."""
    parent: dict[str, str] = {}

    def root(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for pair in pairs:
        parent[root(pair.src)] = root(pair.dst)

    grouped: dict[str, list[ConflictPair]] = defaultdict(list)
    for pair in pairs:
        grouped[root(pair.src)].append(pair)
    return list(grouped.values())


def read_cluster(pairs: Sequence[ConflictPair], events: Sequence[SnapshotEvent]) -> Reading:
    if any(pair.is_possible_second_session for pair in pairs):
        return Reading.TWO_SHOWS
    if any(pair.is_same_venue for pair in pairs):
        return Reading.DUPLICATE_LISTING
    if len({(event.country_iso, event.city_name.casefold()) for event in events}) > 1:
        return Reading.WRONG_LINK
    return Reading.DUPLICATE_VENUE


def cluster_id(event_ids: Iterable[str]) -> str:
    """Stable across re-exports, so verdicts stored against it survive a refresh."""
    return hashlib.sha1("|".join(sorted(event_ids)).encode()).hexdigest()[:12]


def neighbourhood(
    graph: EventGraph, event_ids: Iterable[str]
) -> tuple[list[SnapshotNode], list[SnapshotEdge]]:
    nodes: dict[str, SnapshotNode] = {}
    edges: dict[tuple[str, str, str, str], SnapshotEdge] = {}

    def add_node(node_id: str, node_type: str, label: str) -> None:
        if node_id not in nodes:
            degree = graph.degree(node_id) if node_id in graph else None
            nodes[node_id] = SnapshotNode(id=node_id, type=node_type, label=label, degree=degree)

    for event_id in event_ids:
        if event_id not in graph:
            continue
        event = graph.node(event_id)
        add_node(event_id, event.node_type.value, event.label or event.natural_key)
        for edge, other in graph.incident(event_id, predicates=EVENT_CONTEXT_PREDICATES):
            add_node(other.node_id, other.node_type.value, other.label or other.natural_key)
            key = (edge.src_node_id, edge.predicate.value, edge.dst_node_id, edge.source)
            edges[key] = SnapshotEdge(
                source=edge.src_node_id,
                target=edge.dst_node_id,
                predicate=edge.predicate.value,
                provenance=edge.source,
                source_class=edge.source_class.value,
                confidence=round(edge.confidence, 3),
            )
            if other.node_type is NodeType.VENUE:
                for city_edge, city in graph.incident(
                    other.node_id, predicates=frozenset({Predicate.IN_CITY})
                ):
                    add_node(city.node_id, city.node_type.value, city.label or city.natural_key)
                    city_key = (
                        city_edge.src_node_id,
                        city_edge.predicate.value,
                        city_edge.dst_node_id,
                        city_edge.source,
                    )
                    edges[city_key] = SnapshotEdge(
                        source=city_edge.src_node_id,
                        target=city_edge.dst_node_id,
                        predicate=city_edge.predicate.value,
                        provenance=city_edge.source,
                        source_class=city_edge.source_class.value,
                        confidence=round(city_edge.confidence, 3),
                    )
    return list(nodes.values()), list(edges.values())


def build_cluster(
    pairs: Sequence[ConflictPair], events: dict[str, SnapshotEvent], graph: EventGraph
) -> SnapshotCluster:
    event_ids = sorted({pair.src for pair in pairs} | {pair.dst for pair in pairs})
    cluster_events = sorted(
        (events[event_id] for event_id in event_ids if event_id in events),
        key=lambda event: (event.starts_at, event.title),
    )
    nodes, edges = neighbourhood(graph, event_ids)
    artist_ids = sorted({artist for pair in pairs for artist in pair.shared_artists})
    artists = [
        graph.node(artist_id).label if artist_id in graph else artist_id for artist_id in artist_ids
    ]
    return SnapshotCluster(
        id=cluster_id(event_ids),
        event_date=pairs[0].event_date,
        confidence=max(pair.confidence for pair in pairs),
        reading=read_cluster(pairs, cluster_events),
        artists=artists,
        events=cluster_events,
        pairs=[
            SnapshotPair(
                src=pair.src,
                dst=pair.dst,
                confidence=pair.confidence,
                is_same_venue=pair.is_same_venue,
                is_possible_second_session=pair.is_possible_second_session,
                shared_artists=pair.shared_artists,
            )
            for pair in pairs
        ],
        nodes=nodes,
        edges=edges,
    )


def git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "unknown"


@click.command()
@click.option(
    "--load",
    "load_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="A slice saved by `event-graph graph build --save`.",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=DEFAULT_OUTPUT,
    show_default=True,
    help="Where to write the snapshot the app reads.",
)
def main(load_path: Path, output: Path) -> None:
    """Export the Spanish conflict worklist for the Disco app."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

    try:
        pairs = fetch_conflict_pairs()
    except OperationalError as error:
        raise click.ClickException(
            f"Could not reach ClickHouse ({error}). Is the aws-service-access tunnel up?"
        ) from error
    if not pairs:
        raise click.ClickException("No upcoming conflicts found; check the edge table in .env.")
    logger.info("fetched %d conflict pairs", len(pairs))

    saved = load_graph(load_path)
    logger.info("loaded %s", saved.describe())

    event_ids = {pair.src for pair in pairs} | {pair.dst for pair in pairs}
    in_slice = {event_id for event_id in event_ids if event_id in saved.graph}
    events = fetch_events(event_ids, in_slice)
    logger.info("%d of %d conflicting events are in the slice", len(in_slice), len(event_ids))

    clusters = [build_cluster(group, events, saved.graph) for group in cluster_pairs(pairs)]
    clusters.sort(
        key=lambda cluster: (
            -cluster.confidence,
            READING_ORDER[cluster.reading],
            -len(cluster.events),
            cluster.event_date,
        )
    )

    snapshot = Snapshot(
        exported_at=datetime.now(UTC).isoformat(timespec="seconds"),
        git_commit=git_commit(),
        slice=saved.metadata.slice.describe(),
        rules=[
            "Two upcoming events share a linked artist on the same date, at least one in Spain.",
            "Festivals are excluded: artists link to a festival as a whole, not to their day.",
            "Placeholder artists (Unbekannt, TBA, TBC, Various Artists, ...) are excluded.",
            "Confidence 0.9 same venue, 0.8 several shared artists, 0.6 otherwise; "
            "0.3 when a title names a second session.",
        ],
        clusters=clusters,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(snapshot.model_dump_json())
    logger.info("wrote %d clusters to %s", len(clusters), output)
    click.echo(f"Wrote {len(clusters)} clusters ({len(pairs)} pairs) to {output}")


if __name__ == "__main__":
    main()
