"""How much of the upcoming catalogue is affected by duplicated artists and venues?

Entity resolution is a prerequisite for the rest of the work: venue history is
under-counted and artist candidate sets are polluted until duplicate rows are merged. This
sizes that prize, and prints the common-word artist names that need blocklisting before
any title matching runs.

Counting every name collision badly overstates it. Most colliding venue rows are redirect
history that has already been merged, and a generic name shared across cities is not a
duplicate at all -- there are 33 venues called "Stadthalle". Live rows only, and venues
keyed on name *and* city.

    uv run python bin/duplicate_name_reach.py
    uv run python bin/duplicate_name_reach.py --naive   # the over-count, for comparison
"""

import click

from event_graph.clickhouse import get_client

UPCOMING = "e.event_start_at > now() AND coalesce(e.event_is_redirected, 0) = 0"

LIVE_VENUES = """
    SELECT venue_id, lower(trim(venue_name)) AS name, city_id
    FROM dbt.dim_venues
    WHERE coalesce(venue_is_redirected, 0) = 0 AND coalesce(venue_is_deleted, false) = false
"""

# dim_artists carries no redirect or deleted flag, so the live set comes from staging
LIVE_ARTISTS = """
    SELECT a.pk_dim_artists AS pk, lower(trim(a.artist_name)) AS name
    FROM dbt.dim_artists AS a
    INNER JOIN dbt_dev.stg_website__artist AS s ON s.artist_id = a.artist_id
    WHERE s.artist_redirected_to_artist_id IS NULL
      AND coalesce(s.artist_is_deleted, false) = false
"""


@click.command()
@click.option("--worst", default=15, type=int, help="How many worst collisions to print.")
@click.option("--naive", is_flag=True, help="Also show the every-row, name-only count.")
def main(worst: int, naive: bool) -> None:
    client = get_client()

    upcoming_total = int(
        client.query(f"SELECT count() FROM dbt.dim_events AS e WHERE {UPCOMING}").result_rows[0][0]
    )
    click.echo(f"upcoming non-redirected events: {upcoming_total:,}")

    venue_groups, venue_surplus = client.query(
        f"""
        SELECT count(), sum(n) - count()
        FROM (SELECT name, city_id, count() AS n FROM ({LIVE_VENUES})
              GROUP BY name, city_id HAVING n > 1)
        """
    ).result_rows[0]
    venue_reach = int(
        client.query(
            f"""
            WITH dup AS (
                SELECT name, city_id FROM ({LIVE_VENUES}) GROUP BY name, city_id HAVING count() > 1
            )
            SELECT count()
            FROM dbt.dim_events AS e
            INNER JOIN ({LIVE_VENUES}) AS v ON v.venue_id = e.venue_id
            INNER JOIN dup AS d ON d.name = v.name AND d.city_id = v.city_id
            WHERE {UPCOMING}
            """
        ).result_rows[0][0]
    )

    artist_groups, artist_surplus = client.query(
        f"SELECT count(), sum(n) - count() FROM "
        f"(SELECT name, count() AS n FROM ({LIVE_ARTISTS}) GROUP BY name HAVING n > 1)"
    ).result_rows[0]
    artist_reach = int(
        client.query(
            f"""
            WITH dup AS (SELECT name FROM ({LIVE_ARTISTS}) GROUP BY name HAVING count() > 1)
            SELECT count(DISTINCT b.fk_dim_events)
            FROM dbt.bridge_event_artists AS b
            INNER JOIN ({LIVE_ARTISTS}) AS a ON a.pk = b.fk_dim_artists
            INNER JOIN dup AS d ON d.name = a.name
            INNER JOIN dbt.dim_events AS e ON e.pk_dim_events = b.fk_dim_events
            WHERE {UPCOMING}
            """
        ).result_rows[0][0]
    )

    click.echo("\nlive duplicates (venues keyed on name + city, artists on name)")
    click.echo(f"  {'':<8} {'groups':>9} {'surplus':>9} {'upcoming events':>17}")
    click.echo(
        f"  {'artist':<8} {int(artist_groups):>9,} {int(artist_surplus):>9,} "
        f"{artist_reach:>10,} ({artist_reach / upcoming_total:.1%})"
    )
    click.echo(
        f"  {'venue':<8} {int(venue_groups):>9,} {int(venue_surplus):>9,} "
        f"{venue_reach:>10,} ({venue_reach / upcoming_total:.1%})"
    )

    if naive:
        click.echo("\nnaive count -- every row, name only, redirected included")
        for entity, table, column in (
            ("artist", "dbt.dim_artists", "artist_name"),
            ("venue", "dbt.dim_venues", "venue_name"),
        ):
            row = client.query(
                f"SELECT countIf(n > 1), sum(n) - countIf(n > 1), max(n) FROM "
                f"(SELECT lower(trim({column})) AS k, count() AS n FROM {table} "
                f"GROUP BY k HAVING n > 1)"
            ).result_rows[0]
            click.echo(
                f"  {entity:<8} names {int(row[0]):>7,}  surplus {int(row[1]):>7,}  "
                f"worst {int(row[2]):>4,} rows on one name"
            )

    collisions = client.query(
        f"SELECT name, count() AS n FROM ({LIVE_ARTISTS}) "
        f"GROUP BY name HAVING n > 1 ORDER BY n DESC LIMIT {int(worst)}"
    ).result_rows
    click.echo("\nworst artist collisions -- the blocklist title matching needs")
    click.echo("  " + ", ".join(f"{name} ({int(n)})" for name, n in collisions))


if __name__ == "__main__":
    main()
