"""How many events can have an attribute filled in from a redirect sibling?

An admin redirect asserts that two event rows are the same thing, so any attribute one of
them carries can be transferred to the others in its cluster. This measures how far that
reaches, across four populations -- which population you pick turns out to decide whether
the answer is thousands or single digits.

    uv run python bin/redirect_transfer_coverage.py
    uv run python bin/redirect_transfer_coverage.py --keep-sinks  # what deletion placeholders cost
"""

import sys
from collections.abc import Callable
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import click

sys.path.insert(0, str(Path(__file__).parent))

from _redirects import (  # noqa: E402
    EVENTS_TABLE,
    EventFacts,
    load_artist_sets,
    load_event_facts,
    load_redirect_pairs,
    sink_event_ids,
)

from event_graph.clickhouse import get_client  # noqa: E402

type Population = Callable[[EventFacts], bool]

POPULATIONS: dict[str, Population] = {
    "all events, any date or status": lambda f: True,
    "live only (not redirected)": lambda f: f.is_live,
    "upcoming only": lambda f: f.is_upcoming,
    "upcoming AND live": lambda f: f.is_upcoming and f.is_live,
}


@dataclass(frozen=True, slots=True)
class Funnel:
    """One attribute measured over one population."""

    lacking: int = 0
    reachable: int = 0
    agreed: int = 0
    conflicted: int = 0

    def plus(self, *, lacking: int, reachable: int, agreed: int, conflicted: int) -> "Funnel":
        return Funnel(
            self.lacking + lacking,
            self.reachable + reachable,
            self.agreed + agreed,
            self.conflicted + conflicted,
        )


def measure(
    components: list[frozenset[str]],
    facts: dict[str, EventFacts],
    holds: Callable[[EventFacts], bool],
    value_of: Callable[[str], object],
    in_population: Population,
) -> Funnel:
    """Count events lacking an attribute that a component sibling could supply."""
    funnel = Funnel()
    for component in components:
        members = [f for member in component if (f := facts.get(member)) is not None]
        lack = [f for f in members if in_population(f) and not holds(f)]
        if not lack:
            continue
        donors = [f for f in members if holds(f)]
        if not donors:
            funnel = funnel.plus(lacking=len(lack), reachable=0, agreed=0, conflicted=0)
            continue
        values = {value_of(f.event_id) for f in donors}
        values.discard(None)
        conflict = len(values) > 1
        funnel = funnel.plus(
            lacking=len(lack),
            reachable=len(lack),
            agreed=0 if conflict else len(lack),
            conflicted=len(lack) if conflict else 0,
        )
    return funnel


def donor_shape(
    components: list[frozenset[str]],
    facts: dict[str, EventFacts],
    artist_sets: dict[str, frozenset[int]],
    in_population: Population,
) -> Counter[str]:
    """Classify multi-donor clusters: is a disagreement real, or just incompleteness?

    Deciding what to do when a cluster holds several values turns on this. Donor sets that
    overlap mean one side simply recorded fewer artists; only pairwise-disjoint sets are a
    genuine contradiction, and those are the ones worth withholding from a worklist.
    """
    shape: Counter[str] = Counter()
    for component in components:
        members = [f for member in component if (f := facts.get(member)) is not None]
        lack = [f for f in members if in_population(f) and not f.has_artist]
        if not lack:
            continue
        sets = {
            artist_sets[f.event_id]
            for f in members
            if f.has_artist and f.event_id in artist_sets
        }
        if not sets:
            continue
        if len(sets) == 1:
            shape["single donor value"] += len(lack)
            continue
        ordered = list(sets)
        if any(s == frozenset().union(*sets) for s in sets):
            shape["nested (one donor is a superset)"] += len(lack)
        elif all(
            not (a & b)
            for i, a in enumerate(ordered)
            for b in ordered[i + 1 :]
        ):
            shape["disjoint -- genuine disagreement"] += len(lack)
        else:
            shape["overlapping but neither contains"] += len(lack)
    return shape


@click.command()
@click.option("--keep-sinks", is_flag=True, help="Do not drop deletion-placeholder events.")
@click.option("--conflicts", is_flag=True, help="Break multi-donor clusters down by shape.")
def main(keep_sinks: bool, conflicts: bool) -> None:
    client = get_client()

    click.echo("loading redirect log and event facts...", err=True)
    pairs = load_redirect_pairs(client)
    facts = load_event_facts(client)
    artist_sets = load_artist_sets(client)

    sinks = frozenset() if keep_sinks else sink_event_ids(facts)
    components = pairs.components(exclude=sinks)

    universe = client.query(
        f"SELECT count(), countIf(event_has_artists = 0) FROM {EVENTS_TABLE}"
        " WHERE event_start_at > now() AND coalesce(event_is_redirected, 0) = 0"
    ).result_rows[0]
    upcoming_total, upcoming_artistless = int(universe[0]), int(universe[1])

    click.echo(f"\nredirect pairs                 : {len(pairs.pairs):,}")
    click.echo(f"  self loops (src == dst)      : {pairs.self_loops:,}")
    click.echo(f"  target itself redirected     : {pairs.chained:,}")
    click.echo(f"  sink events excluded         : {len(sinks):,}")
    click.echo(f"components                     : {len(components):,}")
    click.echo(f"  largest                      : {max(len(c) for c in components):,}")

    in_components = [f for f in facts.values() if f.event_id not in sinks]
    click.echo(f"\nevents inside a component      : {len(in_components):,}")
    for label, keep in POPULATIONS.items():
        click.echo(f"  {label:<32} : {sum(1 for f in in_components if keep(f)):>8,}")

    click.echo(f"\nupcoming non-redirected events (whole platform) : {upcoming_total:,}")
    click.echo(
        f"  of which artist-less                          : {upcoming_artistless:,} "
        f"({upcoming_artistless / upcoming_total:.1%})"
    )

    attributes: list[tuple[str, Callable[[EventFacts], bool], Callable[[str], object]]] = [
        ("artist", lambda f: f.has_artist, lambda e: artist_sets.get(e)),
        ("genre", lambda f: f.has_genre, lambda e: None),
        ("ticket provider", lambda f: f.has_provider, lambda e: None),
    ]

    for name, holds, value_of in attributes:
        click.echo(f"\n{name} transfer through the redirect closure")
        click.echo(f"  {'population':<34} {'lacking':>9} {'reachable':>10} {'agree':>8} {'conflict':>9}")
        for label, keep in POPULATIONS.items():
            f = measure(components, facts, holds, value_of, keep)
            click.echo(
                f"  {label:<34} {f.lacking:>9,} {f.reachable:>10,} "
                f"{f.agreed:>8,} {f.conflicted:>9,}"
            )

    artist_target = measure(
        components, facts, lambda f: f.has_artist, lambda e: artist_sets.get(e),
        POPULATIONS["upcoming AND live"],
    )
    if conflicts:
        click.echo("\nartist donor-set shape, where a cluster holds more than one value")
        for label, keep in POPULATIONS.items():
            shape = donor_shape(components, facts, artist_sets, keep)
            total = sum(shape.values())
            click.echo(f"  {label} -- {total:,} reachable")
            for reason, count in shape.most_common():
                click.echo(f"      {reason:<38} {count:>8,}  ({count / total:.1%})")

    click.echo(
        f"\nartist transfer to upcoming live events: {artist_target.reachable:,} of "
        f"{upcoming_artistless:,} artist-less upcoming events = "
        f"{artist_target.reachable / upcoming_artistless:.3%} of the gap"
    )


if __name__ == "__main__":
    main()
