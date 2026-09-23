"""Is a redirect actually an "these two are the same event" assertion?

Not every redirect is a duplicate, so the set needs checking before anything is built on
it. This measures it rather than spot-checking: where both sides of a pair carry artists,
how often do the artist sets agree? That number is what licenses using the redirect log as
labelled ground truth for the genre-propagation and artist-ranking evals.

    uv run python bin/redirect_duplicate_precision.py
"""

import sys
from pathlib import Path

import click

sys.path.insert(0, str(Path(__file__).parent))

from _redirects import EVENTS_TABLE, REDIRECTS_TABLE  # noqa: E402

from event_graph.clickhouse import get_client  # noqa: E402

PAIRS_CTE = f"""
WITH pair AS (
    SELECT event_id AS a, destination_event_id AS b
    FROM {REDIRECTS_TABLE}
    WHERE event_id IS NOT NULL
      AND destination_event_id IS NOT NULL
      AND event_id != destination_event_id
)
"""


@click.command()
@click.option("--samples", default=15, type=int, help="Differing-title examples to print.")
def main(samples: int) -> None:
    client = get_client()

    agreement = client.query(
        f"""{PAIRS_CTE},
        event_artists AS (
            SELECT e.event_id, groupUniqArray(b.fk_dim_artists) AS artists
            FROM {EVENTS_TABLE} AS e
            INNER JOIN dbt.bridge_event_artists AS b ON b.fk_dim_events = e.pk_dim_events
            GROUP BY e.event_id
        )
        SELECT count(),
               countIf(hasAny(xa.artists, xb.artists)),
               countIf(arraySort(xa.artists) = arraySort(xb.artists))
        FROM pair AS p
        INNER JOIN event_artists AS xa ON xa.event_id = p.a
        INNER JOIN event_artists AS xb ON xb.event_id = p.b
        """
    ).result_rows[0]
    both, overlap, identical = (int(v) for v in agreement)

    click.echo("\nredirect pairs where both sides carry artists")
    click.echo(f"  pairs                  : {both:,}")
    click.echo(f"  artist sets overlap    : {overlap:,} ({overlap / both:.1%})")
    click.echo(f"  artist sets identical  : {identical:,} ({identical / both:.1%})")

    shape = client.query(
        f"""{PAIRS_CTE}
        SELECT count(),
               countIf(lower(trim(ta.event_title)) = lower(trim(tb.event_title))),
               countIf(ta.venue_id = tb.venue_id),
               countIf(toDate(ta.event_start_at) = toDate(tb.event_start_at))
        FROM pair AS p
        INNER JOIN {EVENTS_TABLE} AS ta ON ta.event_id = p.a
        INNER JOIN {EVENTS_TABLE} AS tb ON tb.event_id = p.b
        """
    ).result_rows[0]
    pairs, same_title, same_venue, same_date = (int(v) for v in shape)

    click.echo("\nhow much do the two sides look alike?")
    click.echo(f"  pairs                  : {pairs:,}")
    click.echo(f"  identical title        : {same_title:,} ({same_title / pairs:.1%})")
    click.echo(f"  same venue             : {same_venue:,} ({same_venue / pairs:.1%})")
    click.echo(f"  same date              : {same_date:,} ({same_date / pairs:.1%})")

    examples = client.query(
        f"""{PAIRS_CTE}
        SELECT ta.event_title, tb.event_title
        FROM pair AS p
        INNER JOIN {EVENTS_TABLE} AS ta ON ta.event_id = p.a
        INNER JOIN {EVENTS_TABLE} AS tb ON tb.event_id = p.b
        WHERE lower(trim(ta.event_title)) != lower(trim(tb.event_title))
          AND ta.venue_id = tb.venue_id
          AND toDate(ta.event_start_at) = toDate(tb.event_start_at)
        ORDER BY cityHash64(p.a)
        LIMIT {int(samples)}
        """
    ).result_rows

    click.echo("\nadmin-verified name normalisations (same venue, same date, different title)")
    for before, after in examples:
        click.echo(f"  {before!r:<48} -> {after!r}")


if __name__ == "__main__":
    main()
