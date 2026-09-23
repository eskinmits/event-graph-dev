"""Could we surface a duplicate event before ops manually redirects it?

Every redirect in the log is manual ops work already paid for, so the log doubles as a
backtest: take a year of events, apply a candidate rule, and ask how many of the pairs it
proposes were in fact redirected together. A rule that finds them earlier than ops does is
a direct saving.

Two things this can and cannot measure. **Recall is clean** -- a redirect that exists is a
duplicate, so the fraction of real redirects a rule would have surfaced is a fact.
**Precision is a lower bound only**: a pair nobody redirected is ambiguous between "ops
judged them distinct" and "ops never looked", and there is a visible backlog of the latter.
Do not quote the precision column as a precision floor; it needs a labelled sample.

    uv run python bin/duplicate_detection_backtest.py
    uv run python bin/duplicate_detection_backtest.py --year 2023
"""

import sys
from collections import defaultdict
from collections.abc import Callable, Hashable, Sequence
from itertools import combinations
from pathlib import Path
from typing import Any

import click

sys.path.insert(0, str(Path(__file__).parent))

from _redirects import load_event_facts, load_redirect_pairs, sink_event_ids  # noqa: E402

from event_graph.clickhouse import get_client  # noqa: E402

# a venue with more than this many events on one timestamp is a multi-room venue or a
# festival, and every pair inside it would be noise
MAX_GROUP = 12

type Row = tuple[Any, ...]
type Key = Callable[[Row], Hashable]
type Pairwise = Callable[[Row, Row], bool]

EVENT_ID, VENUE, START, TITLE, ARTISTS = range(5)


def backtest(
    rows: Sequence[Row],
    component_of: dict[str, int],
    key: Key,
    extra: Pairwise | None = None,
) -> tuple[int, int]:
    """Return (candidate pairs proposed, pairs that were in fact redirected together)."""
    groups: dict[Hashable, list[Row]] = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)

    proposed = confirmed = 0
    for members in groups.values():
        if not 2 <= len(members) <= MAX_GROUP:
            continue
        for left, right in combinations(members, 2):
            if extra is not None and not extra(left, right):
                continue
            proposed += 1
            component = component_of.get(str(left[EVENT_ID]))
            if component is not None and component == component_of.get(str(right[EVENT_ID])):
                confirmed += 1
    return proposed, confirmed


def shared_artist(left: Row, right: Row) -> bool:
    return bool(set(left[ARTISTS]) & set(right[ARTISTS]))


@click.command()
@click.option("--year", default=2024, type=int, help="Event start year to backtest on.")
def main(year: int) -> None:
    client = get_client()

    pairs = load_redirect_pairs(client)
    facts = load_event_facts(client)
    component_of = {
        member: index
        for index, component in enumerate(pairs.components(exclude=sink_event_ids(facts)))
        for member in component
    }

    timing = client.query(
        """
        SELECT quantiles(0.5, 0.75, 0.9)(
                   dateDiff('day', e.event_first_draft_created_at, r.event_redirected_at)),
               countIf(r.event_redirected_at < e.event_start_at),
               count()
        FROM dbt.stg_backend__event_redirected AS r
        INNER JOIN dbt.dim_events AS e ON e.event_id = r.event_id
        WHERE r.event_redirected_at IS NOT NULL AND e.event_first_draft_created_at IS NOT NULL
        """
    ).result_rows[0]
    median, p75, p90 = (round(q) for q in timing[0])
    click.echo("when ops acts")
    click.echo(f"  days from draft to redirect  : median {median}, p75 {p75}, p90 {p90}")
    click.echo(
        f"  redirected before the event   : {int(timing[1]):,} of {int(timing[2]):,} "
        f"({int(timing[1]) / int(timing[2]):.1%})"
    )

    coverage = client.query(
        """
        WITH pair AS (
            SELECT event_id AS a, destination_event_id AS b
            FROM dbt.stg_backend__event_redirected
            WHERE event_id IS NOT NULL AND destination_event_id IS NOT NULL
              AND event_id != destination_event_id
        )
        SELECT count(),
               countIf(ta.venue_id = tb.venue_id
                       AND toDate(ta.event_start_at) = toDate(tb.event_start_at)),
               countIf(ta.venue_id = tb.venue_id
                       AND toDate(ta.event_start_at) = toDate(tb.event_start_at)
                       AND lower(trim(ta.event_title)) = lower(trim(tb.event_title)))
        FROM pair AS p
        INNER JOIN dbt.dim_events AS ta ON ta.event_id = p.a
        INNER JOIN dbt.dim_events AS tb ON tb.event_id = p.b
        """
    ).result_rows[0]
    total, venue_date, venue_date_title = (int(v) for v in coverage)
    click.echo("\nrecall -- what share of real redirects a rule would have surfaced")
    click.echo(f"  venue + date                  : {venue_date:,} of {total:,} ({venue_date / total:.1%})")
    click.echo(
        f"  venue + date + identical title: {venue_date_title:,} of {total:,} "
        f"({venue_date_title / total:.1%})  <- the Data Audit's method"
    )
    click.echo(
        f"  reachable only without a title match: {venue_date - venue_date_title:,} "
        f"({(venue_date - venue_date_title) / total:.1%}) -- exact title matching cannot see these"
    )

    rows: list[Row] = [
        tuple(row)
        for row in client.query(
        f"""
        SELECT e.event_id, e.venue_id, e.event_start_at, lower(trim(e.event_title)),
               arraySort(arrayFilter(x -> x != '', groupUniqArray(b.fk_dim_artists)))
        FROM dbt.dim_events AS e
        LEFT JOIN dbt.bridge_event_artists AS b ON b.fk_dim_events = e.pk_dim_events
        WHERE e.event_start_at >= '{year}-01-01' AND e.event_start_at < '{year + 1}-01-01'
          AND e.venue_id != 0
        GROUP BY e.event_id, e.venue_id, e.event_start_at, lower(trim(e.event_title))
        """
        ).result_rows
    ]

    rules: list[tuple[str, Key, Pairwise | None]] = [
        ("venue + date", lambda r: (r[VENUE], r[START].date()), None),
        ("venue + date + identical title", lambda r: (r[VENUE], r[START].date(), r[TITLE]), None),
        ("venue + exact start time", lambda r: (r[VENUE], r[START]), None),
        ("venue + exact start time + identical title", lambda r: (r[VENUE], r[START], r[TITLE]), None),
        ("venue + date + shared artist", lambda r: (r[VENUE], r[START].date()), shared_artist),
        ("venue + exact start time + shared artist", lambda r: (r[VENUE], r[START]), shared_artist),
    ]

    click.echo(f"\ncandidate rules backtested on {len(rows):,} events starting in {year}")
    click.echo(f"  {'rule':<44} {'pairs':>9} {'redirected':>12}")
    for name, key, extra in rules:
        proposed, confirmed = backtest(rows, component_of, key, extra)
        click.echo(f"  {name:<44} {proposed:>9,} {confirmed / max(proposed, 1):>11.1%}")
    click.echo("  (the last column is a lower bound -- see this file's docstring)")

    pending = client.query(
        """
        SELECT count(), sum(n) - count() FROM (
            SELECT venue_id, toDate(event_start_at) AS d, lower(trim(event_title)) AS t, count() AS n
            FROM dbt.dim_events
            WHERE event_start_at > now() AND coalesce(event_is_redirected, 0) = 0 AND venue_id != 0
            GROUP BY venue_id, d, t HAVING n > 1)
        """
    ).result_rows[0]
    untitled = client.query(
        """
        SELECT count(), sum(n) - count() FROM (
            SELECT venue_id, toDate(event_start_at) AS d, count() AS n,
                   uniqExact(lower(trim(event_title))) AS titles
            FROM dbt.dim_events
            WHERE event_start_at > now() AND coalesce(event_is_redirected, 0) = 0 AND venue_id != 0
            GROUP BY venue_id, d HAVING n > 1 AND titles = n)
        """
    ).result_rows[0]
    click.echo("\nstanding worklist on the upcoming catalogue")
    click.echo(
        f"  identical title, same venue + date : {int(pending[0]):,} groups, "
        f"{int(pending[1]):,} surplus events"
    )
    click.echo(
        f"  same venue + date, titles all differ: {int(untitled[0]):,} groups, "
        f"{int(untitled[1]):,} surplus events  <- needs disambiguation, not string matching"
    )


if __name__ == "__main__":
    main()
