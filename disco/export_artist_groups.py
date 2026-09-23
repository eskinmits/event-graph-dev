"""Export the artist duplicate groups as a static snapshot for the Disco app's second tab.

Reads what `bin/artist_dedup.py` wrote to `out/artist_dedup/` (the clusters, and the scored
pairs with their evidence) and adds, per artist row, what a reviewer needs to judge it: event
counts, cities and the most recent events, fetched from ClickHouse. Writes one JSON file the
page reads. Rerun after rerunning the dedup:

    uv run python bin/artist_dedup.py                      # on the artist-dedup branch
    uv run python disco/export_artist_groups.py
"""

import csv
import json
import logging
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import click
from clickhouse_connect.driver.exceptions import OperationalError
from clickhouse_connect.driver.external import ExternalData
from pydantic import BaseModel

from event_graph.clickhouse import get_client
from event_graph.config import get_config

logger = logging.getLogger(__name__)

DEFAULT_DEDUP: Final = Path("out/artist_dedup")
DEFAULT_OUTPUT: Final = Path(__file__).parent / "ekg-conflicts" / "data" / "artists.json"
RECENT_EVENTS: Final = 4
# how many shared things of each kind the graph draws, so a big touring act stays readable
GRAPH_CAP: Final[dict[str, int]] = {"night": 4, "venue": 6, "city": 4, "coperformer": 5, "similar": 5}
# measured on the reviewed sample: 100/100 correct at weight >= 0, 19/20 at [-5, 0) (D15)
MERGE_CONFIDENCE: Final = 0.97


class RecentEvent(BaseModel):
    date: str
    title: str
    venue: str
    city: str
    upcoming: bool


class Member(BaseModel):
    artist_id: str
    name: str
    keep: bool
    events: int
    upcoming: int
    cities: list[str]
    recent: list[RecentEvent]


class Link(BaseModel):
    a: str
    b: str
    weight: float
    admin_confirmed: bool


class GraphNode(BaseModel):
    kind: str
    label: str
    keep: bool = False


class Contribution(BaseModel):
    comparison: str
    level: str
    bits: float


class Explanation(BaseModel):
    """The strongest link's score: the prior plus what each comparison added or took away."""

    a: str
    b: str
    prior: float
    contributions: list[Contribution]
    total: float


class Group(BaseModel):
    id: str
    name: str
    size: int
    confidence: float
    admin_confirmed: bool
    upcoming: int
    shared_venues: list[str]
    shared_cities: list[str]
    members: list[Member]
    links: list[Link]
    explanation: Explanation | None
    nodes: list[GraphNode]
    # (row node index, shared-thing node index): the kind is the target node's
    edges: list[tuple[int, int]]


class ArtistSnapshot(BaseModel):
    exported_at: str
    method: list[str]
    groups: list[Group]


def read_clusters(path: Path) -> dict[str, list[dict[str, str]]]:
    clusters: dict[str, list[dict[str, str]]] = defaultdict(list)
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            clusters[row["cluster_id"]].append(row)
    return clusters


def read_levels(report: Path) -> tuple[float, dict[tuple[str, int], str]]:
    data = json.loads(report.read_text())
    if "levels" not in data:
        raise click.ClickException(
            f"{report} predates per-comparison output; rerun `uv run python bin/artist_dedup.py`."
        )
    levels = {(row["comparison"], int(row["gamma"])): row["label"] for row in data["levels"]}
    return float(data["prior_match_weight"]), levels


def explain(row: dict[str, str], prior: float, levels: dict[tuple[str, int], str]) -> Explanation:
    contributions = []
    for column, value in row.items():
        if not column.startswith("gamma_"):
            continue
        comparison, gamma = column.removeprefix("gamma_"), int(float(value))
        factor = float(row.get(f"bf_{comparison}") or 1.0)
        if gamma < 0 or factor <= 0:
            continue  # no data on one side: the comparison says nothing
        contributions.append(
            Contribution(
                comparison=comparison,
                level=levels.get((comparison, gamma), ""),
                bits=round(math.log2(factor), 2),
            )
        )
    contributions.sort(key=lambda c: -abs(c.bits))
    return Explanation(
        a=row["artist_id_l"],
        b=row["artist_id_r"],
        prior=round(prior, 2),
        contributions=contributions,
        total=round(float(row["match_weight"]), 2),
    )


def read_links(
    path: Path, cluster_of: dict[str, str], prior: float, levels: dict[tuple[str, int], str]
) -> tuple[dict[str, list[Link]], dict[str, Explanation]]:
    """Scored pairs whose two rows ended up in the same group, strongest first."""
    links: dict[str, list[Link]] = defaultdict(list)
    strongest: dict[str, tuple[float, Explanation]] = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            a, b = row["artist_id_l"], row["artist_id_r"]
            group = cluster_of.get(a)
            if group is None or cluster_of.get(b) != group:
                continue
            weight = float(row["match_weight"])
            if group not in strongest or weight > strongest[group][0]:
                strongest[group] = (weight, explain(row, prior, levels))
            links[group].append(
                Link(
                    a=a,
                    b=b,
                    weight=round(float(row["match_weight"]), 1),
                    admin_confirmed=row["admin_duplicate"] == "True",
                )
            )
    for group_links in links.values():
        group_links.sort(key=lambda link: -link.weight)
    return links, {group: explanation for group, (_, explanation) in strongest.items()}


def fetch_facts(
    artist_ids: list[str],
) -> tuple[dict[str, tuple[int, int, list[str]]], dict[str, list[RecentEvent]], dict[str, set[str]]]:
    tables = get_config().graph
    ids = ExternalData(
        file_name="ids",
        data="\n".join(f"artist:{a}" for a in artist_ids).encode(),
        fmt="TabSeparated",
        structure=["node_id String"],
    )
    joined = f"""
        from {tables.edges_table} as edges
        inner join {tables.events_table} as events
            on edges.dst_node_id = concat('event:', events.{tables.events_key_column})
        where edges.predicate = 'performs_at'
            and edges.src_node_id in (select node_id from ids)
            and events.event_is_redirected = 0
            and events.event_start_at is not null
    """
    client = get_client()
    counts = {
        node.removeprefix("artist:"): (int(total), int(upcoming), sorted(cities))
        for node, total, upcoming, cities in client.query(
            f"""
            select edges.src_node_id, count(), countIf(events.event_start_at > now()),
                   groupUniqArray(10)(lower(events.city_name))
            {joined}
            group by 1
            """,
            external_data=ids,
        ).result_rows
    }
    recent: dict[str, list[RecentEvent]] = defaultdict(list)
    for node, date, title, venue, city, upcoming in client.query(
        f"""
        select edges.src_node_id, formatDateTime(events.event_start_at, '%Y-%m-%d'),
               events.event_title, events.venue_name, lower(events.city_name),
               events.event_start_at > now()
        {joined}
        order by edges.src_node_id, events.event_start_at desc
        limit {RECENT_EVENTS} by edges.src_node_id
        """,
        external_data=ids,
    ).result_rows:
        recent[node.removeprefix("artist:")].append(
            RecentEvent(date=date, title=title, venue=venue, city=city, upcoming=bool(upcoming))
        )
    venues: dict[str, set[str]] = defaultdict(set)
    for node, venue in client.query(
        f"select distinct edges.src_node_id, events.venue_name {joined}", external_data=ids
    ).result_rows:
        venues[node.removeprefix("artist:")].add(venue)
    return counts, recent, venues


def fetch_graph_facts(artist_ids: list[str]) -> tuple[dict[str, dict[str, dict[str, str]]], dict[str, str]]:
    """Per artist row, the things it could share with another row, keyed by kind, id to label."""
    tables = get_config().graph
    ids = ExternalData(
        file_name="ids",
        data="\n".join(f"artist:{a}" for a in artist_ids).encode(),
        fmt="TabSeparated",
        structure=["node_id String"],
    )
    client = get_client()
    facts: dict[str, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
    for node, venue_id, venue, city, night, title in client.query(
        f"""
        select edges.src_node_id, toString(events.venue_id), events.venue_name, lower(events.city_name),
               formatDateTime(events.event_start_at, '%Y-%m-%d'), events.event_title
        from {tables.edges_table} as edges
        inner join {tables.events_table} as events
            on edges.dst_node_id = concat('event:', events.{tables.events_key_column})
        where edges.predicate = 'performs_at' and edges.src_node_id in (select node_id from ids)
            and events.event_is_redirected = 0 and events.event_start_at is not null
        """,
        external_data=ids,
    ).result_rows:
        row = facts[node.removeprefix("artist:")]
        row["venue"][f"venue:{venue_id}"] = venue
        row["city"][f"city:{city}"] = city.title()
        row["night"][f"night:{night}|{venue_id}"] = f"{night} · {venue}"
    for node, other in client.query(
        f"""
        with bills as (
            select edges.dst_node_id as event, edges.src_node_id as artist
            from {tables.edges_table} as edges
            inner join {tables.events_table} as events
                on edges.dst_node_id = concat('event:', events.{tables.events_key_column})
            where edges.predicate = 'performs_at' and ifNull(events.event_category, '') != 'festivals'
        )
        select distinct mine.artist, theirs.artist
        from bills as mine inner join bills as theirs on mine.event = theirs.event
        where mine.artist in (select node_id from ids) and mine.artist != theirs.artist
        """,
        external_data=ids,
    ).result_rows:
        facts[node.removeprefix("artist:")]["coperformer"][other] = other
    for node, other in client.query(
        f"""
        select src_node_id, dst_node_id from {tables.edges_table}
        where predicate = 'similar_to' and src_node_id in (select node_id from ids)
        """,
        external_data=ids,
    ).result_rows:
        facts[node.removeprefix("artist:")]["similar"][other] = other

    others = sorted(
        {n for row in facts.values() for kind in ("coperformer", "similar") for n in row[kind]}
    )
    names: dict[str, str] = {}
    if others:
        wanted = ExternalData(
            file_name="wanted", data="\n".join(others).encode(), fmt="TabSeparated",
            structure=["node_id String"],
        )
        names = {
            str(node): str(label)
            for node, label in client.query(
                f"select {tables.node_id_column}, label from {tables.nodes_table}"
                f" where {tables.node_id_column} in (select node_id from wanted)",
                external_data=wanted,
            ).result_rows
        }
    return facts, names


def group_graph(
    members: list[Member],
    facts: dict[str, dict[str, dict[str, str]]],
    names: dict[str, str],
) -> tuple[list[GraphNode], list[tuple[int, int]]]:
    """The rows, and only what two or more of them share: the evidence, drawn."""
    nodes = [GraphNode(kind="row", label=m.name, keep=m.keep) for m in members]
    row_index = {m.artist_id: index for index, m in enumerate(members)}
    edges: list[tuple[int, int]] = []
    for kind, cap in GRAPH_CAP.items():
        holders: dict[str, set[str]] = defaultdict(set)
        labels: dict[str, str] = {}
        for m in members:
            for key, label in facts.get(m.artist_id, {}).get(kind, {}).items():
                holders[key].add(m.artist_id)
                labels[key] = names.get(label, label) if kind in ("coperformer", "similar") else label
        common = sorted(
            (key for key, rows in holders.items() if len(rows) >= 2),
            key=lambda key: (-len(holders[key]), labels[key]),
        )[:cap]
        for key in common:
            nodes.append(GraphNode(kind=kind, label=labels[key]))
            edges.extend((row_index[artist], len(nodes) - 1) for artist in sorted(holders[key]))
    return nodes, edges


def shared(values: list[set[str]]) -> list[str]:
    """Values at least two of the rows have in common: what ties the group together."""
    seen: dict[str, int] = defaultdict(int)
    for per_row in values:
        for value in per_row:
            seen[value] += 1
    return sorted(value for value, rows in seen.items() if rows >= 2 and value)


@click.command()
@click.option("--dedup", default=DEFAULT_DEDUP, type=click.Path(file_okay=False, path_type=Path))
@click.option("--output", default=DEFAULT_OUTPUT, type=click.Path(dir_okay=False, path_type=Path))
def main(dedup: Path, output: Path) -> None:
    """Export the artist duplicate groups for the Disco app."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    clusters_csv, pairs_csv = dedup / "clusters.csv", dedup / "pairs.csv"
    for required in (clusters_csv, pairs_csv, dedup / "report.json"):
        if not required.exists():
            raise click.ClickException(
                f"{required} is missing; run `uv run python bin/artist_dedup.py` first."
            )

    prior, levels = read_levels(dedup / "report.json")
    clusters = read_clusters(clusters_csv)
    cluster_of = {row["artist_id"]: cid for cid, rows in clusters.items() for row in rows}
    links, explanations = read_links(pairs_csv, cluster_of, prior, levels)
    logger.info("read %d groups over %d rows", len(clusters), len(cluster_of))

    try:
        counts, recent, venues = fetch_facts(sorted(cluster_of))
        facts, names = fetch_graph_facts(sorted(cluster_of))
    except OperationalError as error:
        raise click.ClickException(
            f"Could not reach ClickHouse ({error}). Is the aws-service-access tunnel up?"
        ) from error

    groups = []
    for cid, rows in clusters.items():
        members = []
        for row in rows:
            total, upcoming, cities = counts.get(row["artist_id"], (0, 0, []))
            members.append(
                Member(
                    artist_id=row["artist_id"],
                    name=row["name"],
                    keep=row["canonical"] == "True",
                    events=total,
                    upcoming=upcoming,
                    cities=cities,
                    recent=recent.get(row["artist_id"], []),
                )
            )
        members.sort(key=lambda m: (not m.keep, -m.events, m.name))
        group_links = links.get(cid, [])
        nodes, edges = group_graph(members, facts, names)
        groups.append(
            Group(
                id=cid,
                name=members[0].name,
                size=len(members),
                confidence=MERGE_CONFIDENCE,
                admin_confirmed=any(link.admin_confirmed for link in group_links),
                upcoming=sum(m.upcoming for m in members),
                shared_venues=shared([venues.get(m.artist_id, set()) for m in members]),
                shared_cities=shared([set(m.cities) for m in members]),
                members=members,
                links=group_links,
                explanation=explanations.get(cid),
                nodes=nodes,
                edges=edges,
            )
        )
    groups.sort(key=lambda g: (-g.upcoming, -g.size, g.name.casefold()))

    snapshot = ArtistSnapshot(
        exported_at=datetime.now(UTC).isoformat(timespec="seconds"),
        method=[
            "Artist rows that share a normalised name are compared on graph evidence: the same "
            "night at the same venue, shared venues, cities, co-performers, similar artists and genres.",
            "The weights are learned without labels (Fellegi-Sunter, Splink), and a merge is refused "
            "when two rows show strong evidence of being different acts.",
            "Checked by people: 100 of 100 reviewed merges were the same act (at least 97% precise), "
            "and it finds 89.6% of the duplicates admins had already confirmed.",
        ],
        groups=groups,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(snapshot.model_dump_json(exclude_defaults=True))
    click.echo(
        f"Wrote {len(groups):,} groups ({sum(g.size for g in groups):,} rows,"
        f" {sum(1 for g in groups if g.upcoming):,} with upcoming events) to {output}"
    )


if __name__ == "__main__":
    main()
