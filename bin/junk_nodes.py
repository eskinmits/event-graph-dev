"""Which graph nodes are junk hubs -- one node standing in for many unrelated real things?

A junk hub fuses every cluster it touches: two events that share "Unbekannt" as their
artist look related, and a walk through it reaches thousands of events that have nothing to
do with each other. The hand-written lists in `graph/filters.py` and the dbt
`conflicts_with` draft already disagree with each other and miss newly created rows (a
second Unbekannt appeared), so this derives the list from data instead.

Each detector is a rule that can be read and re-implemented in dbt:

- **artist, impossible schedule.** A performer is in one city per night. Events in 3+
  cities on one date, on several dates, means the node stands for more than one act. That
  alone catches four kinds of node, only two of which are junk, so it is combined with:
- **artist, title mention.** A real act, a theme night and a touring production are named
  in their events' titles; a placeholder never is. Unbekannt is mentioned in 0% of its
  events' titles, Various Artists in 9%.
- **artist, automation share.** Common-word acts (Thursday, Train, Red) get attached by
  title automation to any title containing the word. The node is real and the edges are
  wrong, so these go to review, never to the filter.
- **venue named after its own city.** "Amsterdam" in Amsterdam holds 7k unrelated events.
- **placeholder vocabulary** for artists, venues and organizer brands, and the deletion-sink
  titles for events that redirects drain into.

`junk` means filter it: flag the node, keep its edges. `review` means the node is probably
real but its links are not, so filtering it would hide the problem rather than fix it.

    uv run python bin/junk_nodes.py
    uv run python bin/junk_nodes.py --out junk_nodes.csv --show 40
"""

import csv
import json
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

import click
from clickhouse_connect.driver.client import Client
from pydantic import BaseModel, ConfigDict

sys.path.insert(0, str(Path(__file__).parent))

from _redirects import SINK_TITLES  # noqa: E402

from event_graph.clickhouse import get_client  # noqa: E402
from event_graph.config import get_config  # noqa: E402
from event_graph.graph.filters import DEFAULT_JUNK_HUB_LABELS  # noqa: E402

# 1% of artists ever reach 3 cities on one date; 3 such dates rules out a one-off data slip
CLASH_CITIES: Final = 3
MIN_CLASH_DATES: Final = 3
# below this a clashing artist is not named in its own events' titles
MAX_PLACEHOLDER_MENTION: Final = 0.2
# above this share of links from title automation, the node is a common-word magnet
MIN_AUTOMATION_SHARE: Final = 0.3
# a village venue named after its village with a handful of events is plausibly real
MIN_CITY_VENUE_EVENTS: Final = 20

AUTOMATED_SOURCES: Final[tuple[str, ...]] = ("automation", "auto-matching-rule")

# "Red Band" is the band Red: its titles say "Red", so the suffix must go before matching
DISAMBIGUATION_SUFFIXES: Final[tuple[str, ...]] = (
    "band", "oficial", "official", "music", "dj", "live", "group",
)

# case-folded, trimmed, whole-label matches. "?+" catches labels whose script was lost
# to mojibake, which are unreadable whatever they once said
PLACEHOLDER_PATTERNS: Final[tuple[str, ...]] = (
    "unbekannt", "unknown", "desconocido", "onbekend", "inconnu", "sconosciuto",
    "tba", "tbc", "tbd", "t\\.b\\.a\\.?", "to be announced", "to be confirmed",
    "n/a", "na", "none", "null", "-+", "\\.+", "\\?+", "test",
    "various", "various artists", "varios artistas", "diverse artiesten",
    "verschiedene künstler", "artistes variés", "va", "v\\.a\\.",
    "special guests?", "secret guests?", "surprise act", "guests?",
    "dj", "djs", "resident djs?", "line.?up", "lineup tba", "more tba", "and more", "& more",
    "live", "live band", "artists?", "headliner", "support", "support act", "auditorium",
)
THEME_PATTERNS: Final[tuple[str, ...]] = (
    "halloween", "halloween party", "christmas", "kerst", "oktoberfest",
    "fête de la musique", "open mic", "karaoke", "new year'?s eve", "nye", "silvester",
    "oudejaarsavond", "carnaval", "carnival", "koningsdag", "kingsday",
    "saturday night", "friday night",
)
VENUE_PLACEHOLDER_PATTERNS: Final[tuple[str, ...]] = (
    "tba", "tbc", "tbd", "to be announced", "secret location", "geheime locatie",
    "secret", "online", "livestream", "live ?stream", "virtual", "unknown", "unbekannt",
    "onbekend", "various venues", "diverse locaties", "n/a", "-+", "\\?+", "test",
)


def whole_label(patterns: Iterable[str]) -> str:
    return "^(" + "|".join(patterns) + ")$"


PLACEHOLDER_RE: Final = whole_label(PLACEHOLDER_PATTERNS)
THEME_RE: Final = whole_label(THEME_PATTERNS)
VENUE_PLACEHOLDER_RE: Final = whole_label(VENUE_PLACEHOLDER_PATTERNS)

# the labels someone already judged junk by hand: filters.py, the conflicts_with draft and
# the reverted dbt seed. the detector should recover every one of them without being told
HAND_LABELLED: Final[frozenset[str]] = DEFAULT_JUNK_HUB_LABELS | {
    "tba", "tbc", "various artists", "live", "karaoke",
}


class Verdict(StrEnum):
    JUNK = "junk"
    REVIEW = "review"


class Kind(StrEnum):
    PLACEHOLDER = "placeholder"
    THEME = "theme"
    AUTOMATION_MAGNET = "automation_magnet"
    OVERLOADED = "overloaded"
    CITY_AS_VENUE = "city_as_venue"
    DELETION_SINK = "deletion_sink"


class JunkNode(BaseModel):
    """One detected node, with the signals that put it on the list."""

    model_config = ConfigDict(frozen=True)

    node_id: str
    node_type: str
    label: str
    verdict: Verdict
    kind: Kind
    reason: str
    degree: int
    upcoming_events: int
    signals: dict[str, Any]


class ArtistSignals(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: str
    label: str
    degree: int
    upcoming_events: int
    active_dates: int
    clash_dates: int
    max_cities_per_date: int
    mention_rate: float
    automation_share: float
    upcoming_clash_pairs: int
    is_placeholder_label: bool
    is_theme_label: bool


def _escape(pattern: str) -> str:
    return pattern.replace("\\", "\\\\").replace("'", "\\'")


def load_artist_signals(client: Client) -> list[ArtistSignals]:
    """Signals for every artist that clashes or carries a vocabulary label.

    Festivals are excluded from the schedule check: an artist links to a festival as a
    whole, so a festival and the artist's own set on the same date is not a clash.
    """
    tables = get_config().graph
    query = f"""
    WITH
    labels AS (
        SELECT {tables.node_id_column} AS node_id, label,
               lower(trim(label)) AS folded,
               trim(replaceRegexpAll(
                   replaceRegexpAll(folded, '\\\\s*\\\\([^)]*\\\\)', ''),
                   '\\\\s+({"|".join(DISAMBIGUATION_SUFFIXES)})$', ''
               )) AS core
        FROM {tables.nodes_table} WHERE node_type = 'artist'
    ),
    links AS (
        SELECT b.src_node_id AS node_id, b.source AS source,
               toDate(e.event_start_at) AS date, e.city_name AS city,
               lower(e.event_title) AS title,
               e.event_start_at > now() AS is_upcoming,
               coalesce(e.event_is_redirected, 0) = 0
                   AND ifNull(e.event_category, '') != 'festivals'
                   AND e.event_start_at IS NOT NULL AS counts_for_schedule
        FROM {tables.edges_table} AS b
        INNER JOIN {tables.events_table} AS e
            ON e.{tables.events_key_column} = substring(b.dst_node_id, length('event:') + 1)
        WHERE b.predicate = 'performs_at'
    ),
    per_date AS (
        SELECT node_id, date, uniqExact(city) AS cities, count() AS n,
               countIf(is_upcoming) AS upcoming
        FROM links WHERE counts_for_schedule GROUP BY node_id, date
    ),
    schedule AS (
        SELECT node_id, count() AS active_dates,
               countIf(cities >= {CLASH_CITIES}) AS clash_dates,
               max(cities) AS max_cities_per_date,
               sum(intDiv(upcoming * (upcoming - 1), 2)) AS upcoming_clash_pairs
        FROM per_date GROUP BY node_id
    ),
    candidates AS (
        SELECT node_id FROM schedule WHERE clash_dates >= {MIN_CLASH_DATES}
        UNION DISTINCT
        SELECT node_id FROM labels
        WHERE match(folded, '{_escape(PLACEHOLDER_RE)}') OR match(folded, '{_escape(THEME_RE)}')
    ),
    link_stats AS (
        SELECT l.node_id AS node_id, count() AS degree, countIf(l.is_upcoming) AS upcoming_events,
               avg(position(l.title, n.core) > 0) AS mention_rate,
               avg(l.source IN {AUTOMATED_SOURCES}) AS automation_share
        FROM links AS l INNER JOIN labels AS n ON n.node_id = l.node_id
        WHERE l.node_id IN (SELECT node_id FROM candidates)
        GROUP BY l.node_id
    )
    SELECT n.node_id, n.label, s.degree, s.upcoming_events,
           coalesce(sc.active_dates, 0), coalesce(sc.clash_dates, 0),
           coalesce(sc.max_cities_per_date, 0), s.mention_rate, s.automation_share,
           coalesce(sc.upcoming_clash_pairs, 0),
           match(n.folded, '{_escape(PLACEHOLDER_RE)}'), match(n.folded, '{_escape(THEME_RE)}')
    FROM link_stats AS s
    INNER JOIN labels AS n ON n.node_id = s.node_id
    LEFT JOIN schedule AS sc ON sc.node_id = s.node_id
    """
    fields = list(ArtistSignals.model_fields)
    return [
        ArtistSignals.model_validate(dict(zip(fields, row, strict=True)))
        for row in client.query(query).result_rows
    ]


def classify_artist(artist: ArtistSignals) -> JunkNode | None:
    clashes = artist.clash_dates >= MIN_CLASH_DATES
    verdict: Verdict
    kind: Kind
    if artist.is_placeholder_label:
        verdict, kind, reason = Verdict.JUNK, Kind.PLACEHOLDER, "placeholder label"
    elif artist.is_theme_label:
        verdict, kind, reason = Verdict.JUNK, Kind.THEME, "occasion or format, not a performer"
    elif not clashes:
        return None
    elif artist.automation_share >= MIN_AUTOMATION_SHARE:
        verdict, kind = Verdict.REVIEW, Kind.AUTOMATION_MAGNET
        reason = (
            f"{artist.automation_share:.0%} of links from title automation; "
            "the node is probably real, the links are not"
        )
    elif artist.mention_rate < MAX_PLACEHOLDER_MENTION:
        verdict, kind = Verdict.JUNK, Kind.PLACEHOLDER
        reason = f"never named in its events' titles ({artist.mention_rate:.0%})"
    else:
        verdict, kind = Verdict.REVIEW, Kind.OVERLOADED
        reason = "one name for several acts: a work, a tribute circuit or a name collision"

    if clashes:
        reason += (
            f"; {CLASH_CITIES}+ cities on one date {artist.clash_dates}x"
            f" (max {artist.max_cities_per_date})"
        )
    return JunkNode(
        node_id=artist.node_id,
        node_type="artist",
        label=artist.label,
        verdict=verdict,
        kind=kind,
        reason=reason,
        degree=artist.degree,
        upcoming_events=artist.upcoming_events,
        signals=artist.model_dump(exclude={"node_id", "label", "degree", "upcoming_events"}),
    )


def detect_venues(client: Client) -> list[JunkNode]:
    tables = get_config().graph
    query = f"""
    WITH
    venues AS (
        SELECT {tables.node_id_column} AS node_id, label, lower(trim(label)) AS folded
        FROM {tables.nodes_table} WHERE node_type = 'venue'
    ),
    own_city AS (
        SELECT b.src_node_id AS node_id, lower(trim(c.label)) AS city
        FROM {tables.edges_table} AS b
        INNER JOIN {tables.nodes_table} AS c ON c.{tables.node_id_column} = b.dst_node_id
        WHERE b.predicate = 'in_city'
    ),
    countries AS (
        SELECT groupUniqArray(lower(event_country_name)) AS names FROM {tables.events_table}
    ),
    held AS (
        SELECT b.dst_node_id AS node_id, count() AS degree,
               countIf(e.event_start_at > now()) AS upcoming_events
        FROM {tables.edges_table} AS b
        INNER JOIN {tables.events_table} AS e
            ON e.{tables.events_key_column} = substring(b.src_node_id, length('event:') + 1)
        WHERE b.predicate = 'held_at'
        GROUP BY b.dst_node_id
    )
    SELECT v.node_id, v.label, h.degree, h.upcoming_events,
           match(v.folded, '{_escape(VENUE_PLACEHOLDER_RE)}') AS is_placeholder,
           arrayMap(part -> trim(part), splitByChar(',', v.folded)) AS parts,
           parts[1] = c.city
               AND (length(parts) = 1
                    OR has((SELECT names FROM countries), parts[-1])
                    OR match(parts[-1], '^[a-z]{{2}}$')) AS is_city
    FROM venues AS v
    INNER JOIN held AS h ON h.node_id = v.node_id
    LEFT JOIN own_city AS c ON c.node_id = v.node_id
    WHERE is_placeholder OR is_city
    """
    found = []
    for node_id, label, degree, upcoming, is_placeholder, _, is_city in client.query(
        query
    ).result_rows:
        if is_placeholder:
            verdict, kind, reason = Verdict.JUNK, Kind.PLACEHOLDER, "placeholder venue name"
        elif degree >= MIN_CITY_VENUE_EVENTS:
            verdict, kind, reason = Verdict.JUNK, Kind.CITY_AS_VENUE, "named after its own city"
        else:
            verdict, kind = Verdict.REVIEW, Kind.CITY_AS_VENUE
            reason = f"named after its own city, but only {degree} events"
        found.append(
            JunkNode(
                node_id=node_id,
                node_type="venue",
                label=label,
                verdict=verdict,
                kind=kind,
                reason=reason,
                degree=int(degree),
                upcoming_events=int(upcoming),
                signals={},
            )
        )
    return found


def detect_organizer_brands(client: Client) -> list[JunkNode]:
    """Vocabulary only, and review only: brands have not been measured as hubs yet."""
    tables = get_config().graph
    query = f"""
    SELECT n.{tables.node_id_column}, n.label, count() AS degree
    FROM {tables.nodes_table} AS n
    INNER JOIN {tables.edges_table} AS b ON b.dst_node_id = n.{tables.node_id_column}
    WHERE n.node_type = 'organizer_brand' AND b.predicate = 'promoted_by'
      AND match(lower(trim(n.label)), '{_escape(PLACEHOLDER_RE)}')
    GROUP BY 1, 2
    """
    return [
        JunkNode(
            node_id=node_id,
            node_type="organizer_brand",
            label=label,
            verdict=Verdict.REVIEW,
            kind=Kind.PLACEHOLDER,
            reason="placeholder brand name; brands are not yet measured as hubs",
            degree=int(degree),
            upcoming_events=0,
            signals={},
        )
        for node_id, label, degree in client.query(query).result_rows
    ]


def detect_event_sinks(client: Client) -> list[JunkNode]:
    """Deletion placeholders that redirects drain into, fusing every cluster they touch."""
    tables = get_config().graph
    titles = ", ".join(f"'{_escape(title)}'" for title in sorted(SINK_TITLES))
    query = f"""
    SELECT n.{tables.node_id_column}, n.label, count() AS degree
    FROM {tables.nodes_table} AS n
    INNER JOIN {tables.edges_table} AS b ON b.dst_node_id = n.{tables.node_id_column}
    WHERE n.node_type = 'event' AND b.predicate = 'same_as'
      AND lower(trim(n.label)) IN ({titles})
    GROUP BY 1, 2
    """
    return [
        JunkNode(
            node_id=node_id,
            node_type="event",
            label=label,
            verdict=Verdict.JUNK,
            kind=Kind.DELETION_SINK,
            reason=f"deletion placeholder, {degree} redirects drain into it",
            degree=int(degree),
            upcoming_events=0,
            signals={},
        )
        for node_id, label, degree in client.query(query).result_rows
    ]


UPCOMING_PAIRS_PER_ARTIST: Final = """
    SELECT b.src_node_id AS node_id, count() AS degree,
           sumIf(pairs, is_upcoming) AS upcoming_clash_pairs
    FROM (
        SELECT b.src_node_id, toDate(e.event_start_at) AS date,
               e.event_start_at > now() AND coalesce(e.event_is_redirected, 0) = 0
                   AND ifNull(e.event_category, '') != 'festivals' AS is_upcoming,
               count() AS n, intDiv(n * (n - 1), 2) AS pairs
        FROM {edges} AS b
        INNER JOIN {events} AS e ON e.{key} = substring(b.dst_node_id, length('event:') + 1)
        WHERE b.predicate = 'performs_at'
        GROUP BY b.src_node_id, date, is_upcoming
    ) AS b
    GROUP BY b.src_node_id
"""


def artist_pair_counts(client: Client) -> tuple[int, list[tuple[str, int, int]]]:
    """Total upcoming same-artist same-date pairs, and (node_id, degree, pairs) per artist.

    Degree counts every link, pairs only upcoming non-festival ones -- the same cut the
    `conflicts_with` draft sizes itself on.
    """
    tables = get_config().graph
    rows = client.query(
        UPCOMING_PAIRS_PER_ARTIST.format(
            edges=tables.edges_table, events=tables.events_table, key=tables.events_key_column
        )
    ).result_rows
    per_artist = [(str(node_id), int(degree), int(pairs)) for node_id, degree, pairs in rows]
    return sum(pairs for _, _, pairs in per_artist), per_artist


def write_csv(nodes: Sequence[JunkNode], path: Path) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["node_id", "node_type", "label", "verdict", "kind", "reason",
             "degree", "upcoming_events", "signals"]
        )
        for node in nodes:
            writer.writerow(
                [node.node_id, node.node_type, node.label, node.verdict, node.kind,
                 node.reason, node.degree, node.upcoming_events,
                 json.dumps(node.signals, sort_keys=True)]
            )


@click.command()
@click.option(
    "--out",
    default=Path("junk_nodes.csv"),
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where to write the full list.",
)
@click.option("--show", default=25, type=int, help="How many rows per verdict to print.")
def main(out: Path, show: int) -> None:
    client = get_client()

    artist_signals = load_artist_signals(client)
    artists = [node for a in artist_signals if (node := classify_artist(a)) is not None]
    nodes = sorted(
        artists + detect_venues(client) + detect_organizer_brands(client)
        + detect_event_sinks(client),
        key=lambda n: (n.verdict != Verdict.JUNK, -n.degree),
    )
    write_csv(nodes, out)

    click.echo(f"wrote {len(nodes):,} nodes to {out}\n")
    counts = Counter((n.node_type, n.verdict, n.kind) for n in nodes)
    click.echo(f"  {'type':<16} {'verdict':<8} {'kind':<18} {'nodes':>7} {'edges':>9}")
    for (node_type, verdict, kind), count in sorted(counts.items()):
        edges = sum(
            n.degree for n in nodes if (n.node_type, n.verdict, n.kind) == (node_type, verdict, kind)
        )
        click.echo(f"  {node_type:<16} {verdict:<8} {kind:<18} {count:>7,} {edges:>9,}")

    junk_artists = [n for n in artists if n.verdict == Verdict.JUNK]
    junk_ids = {n.node_id for n in junk_artists}
    total, per_artist = artist_pair_counts(client)
    removed = sum(pairs for node_id, _, pairs in per_artist if node_id in junk_ids)
    top_by_degree = sorted(per_artist, key=lambda row: row[1], reverse=True)[: len(junk_ids)]
    baseline = sum(pairs for _, _, pairs in top_by_degree)
    baseline_hits = sum(node_id in junk_ids for node_id, _, _ in top_by_degree)
    k = len(junk_ids)
    click.echo(
        f"\nupcoming same-artist same-date pairs (non-festival): {total:,}"
        f"\n  flag the {k} junk artists:           {removed:>7,} pairs removed ({removed / total:.1%})"
        f"\n  flag the {k} highest-degree artists: {baseline:>7,} pairs removed "
        f"({baseline / total:.1%})  <- baseline, {baseline_hits}/{k} of them junk"
    )

    detected = {n.label.casefold().strip() for n in junk_artists}
    missed = sorted(HAND_LABELLED - detected)
    click.echo(
        f"\nhand-labelled junk recovered: {len(HAND_LABELLED) - len(missed)}/{len(HAND_LABELLED)}"
        + (f"  missed: {', '.join(missed)}" if missed else "")
    )
    new = sorted(
        (n for n in nodes if n.verdict == Verdict.JUNK
         and n.label.casefold().strip() not in HAND_LABELLED),
        key=lambda n: -n.degree,
    )
    click.echo(f"junk not on any hand list: {len(new):,}")

    for verdict in Verdict:
        rows = [n for n in nodes if n.verdict == verdict][:show]
        click.echo(f"\ntop {verdict} by degree")
        for n in rows:
            click.echo(
                f"  {n.node_type:<8} {n.label[:32]:<32} {n.degree:>6,}  {n.kind:<18} {n.reason}"
            )


if __name__ == "__main__":
    main()
