import logging
from collections.abc import Callable
from typing import Any

import click

from event_graph.clickhouse import get_client as get_clickhouse_client
from event_graph.graph import (
    DEFAULT_NODE_FILTER,
    BuildMetadata,
    BuildReport,
    ClickHouseGraphSource,
    ENTITY_NODE_TYPES,
    Direction,
    EventGraph,
    GraphBuilder,
    GraphSlice,
    NodeType,
    Predicate,
    keep_everything,
)


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Log what the build is doing.")
def main(verbose: bool) -> None:
    """Event knowledge graph over ClickHouse."""
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


@main.command()
def ping() -> None:
    """Check that the ClickHouse connection works."""
    click.echo(get_clickhouse_client().query("SELECT version()").result_rows[0][0])


def slice_options[F: Callable[..., None]](command: F) -> F:
    """Share the slice flags between every command that builds a projection."""
    for option in reversed(
        (
            click.option("--country", default=None, help="ISO-2 market to slice to, e.g. NL."),
            click.option(
                "--predicate",
                "predicates",
                multiple=True,
                type=click.Choice([p.value for p in Predicate]),
                help="Restrict to these predicates. Repeatable.",
            ),
            click.option(
                "--min-confidence", default=0.0, type=float, help="Drop weaker edges."
            ),
            click.option("--max-edges", default=None, type=int, help="Cap edges loaded."),
            click.option(
                "--keep-junk-hubs",
                is_flag=True,
                help='Keep "Unbekannt" and friends, which will fuse unrelated clusters.',
            ),
            click.option(
                "--prune-above-degree",
                default=None,
                type=int,
                help="Drop nodes whose degree exceeds this once the graph is built.",
            ),
            click.option(
                "--lenient",
                is_flag=True,
                help="Count edges that break the ontology instead of failing the build.",
            ),
        )
    ):
        command = option(command)
    return command


def _build(
    country: str | None,
    predicates: tuple[str, ...],
    min_confidence: float,
    max_edges: int | None,
    keep_junk_hubs: bool,
    prune_above_degree: int | None,
    lenient: bool,
) -> tuple[EventGraph, BuildMetadata, BuildReport]:
    graph_slice = GraphSlice(
        country=country,
        predicates=frozenset(Predicate(p) for p in predicates) or None,
        min_confidence=min_confidence,
        max_edges=max_edges,
    )
    builder = GraphBuilder(
        ClickHouseGraphSource(graph_slice),
        node_filter=keep_everything if keep_junk_hubs else DEFAULT_NODE_FILTER,
        strict=not lenient,
        max_hub_degree=prune_above_degree,
    )
    return builder.build()


@main.group()
def graph() -> None:
    """Build and inspect the in-memory projection."""


@graph.command("build")
@slice_options
def build_graph(**options: Any) -> None:
    """Build the projection and report what it contains."""
    projection, metadata, report = _build(**options)

    click.echo(f"Source:    {metadata.source}")
    click.echo(f"Built at:  {metadata.built_at:%Y-%m-%d %H:%M:%S %Z} (commit {metadata.git_commit})")
    click.echo(report.summary())

    click.echo("\nNodes by type")
    for node_type, count in sorted(
        projection.node_counts().items(), key=lambda item: -item[1]
    ):
        click.echo(f"  {node_type.value:<20} {count:>12,}")

    click.echo("\nEdges by predicate")
    for predicate, count in sorted(
        projection.edge_counts().items(), key=lambda item: -item[1]
    ):
        click.echo(f"  {predicate.value:<20} {count:>12,}")


@graph.command("show")
@click.argument("node_id")
@click.option("--hops", default=2, type=int, help="How far to walk.")
@click.option(
    "--max-degree",
    default=1000,
    type=int,
    help="Do not expand through nodes above this degree.",
)
@click.option(
    "--direction",
    default=Direction.BOTH.value,
    type=click.Choice([d.value for d in Direction]),
)
@click.option(
    "--through-classifications",
    is_flag=True,
    help="Also walk through genre and provider nodes, which connect unrelated events.",
)
@slice_options
def show_neighbourhood(
    node_id: str,
    hops: int,
    max_degree: int,
    direction: str,
    through_classifications: bool,
    **options: Any,
) -> None:
    """Show a node's neighbourhood and the edges that justify each hop.

    NODE_ID is a canonical id such as event:abc-123.
    """
    projection, _, _ = _build(**options)

    neighbourhood = projection.expand(
        node_id,
        hops=hops,
        direction=Direction(direction),
        max_degree=max_degree,
        through=None if through_classifications else ENTITY_NODE_TYPES,
    )
    click.echo(f"{neighbourhood.root}\n")
    for reached in neighbourhood:
        if not reached.hops:
            continue
        click.echo(
            f"  hop {reached.hops}  {reached.confidence:.2f}  "
            f"{reached.node.node_type.value:<18} {reached.node.label}"
        )
        click.echo(f"          via {reached.explain()}")


@graph.command("ontology")
def show_ontology() -> None:
    """Print the node and edge vocabulary this repo understands."""
    from event_graph.graph import ONTOLOGY

    click.echo("Node types")
    for node_type in NodeType:
        click.echo(f"  {node_type.value}")
    click.echo("\nPredicates")
    for spec in ONTOLOGY.values():
        domain = "|".join(sorted(spec.domain))
        range_ = "|".join(sorted(spec.range))
        flags = " ".join(
            flag
            for flag, on in (("symmetric", spec.symmetric), ("derived", spec.derived))
            if on
        )
        click.echo(f"  {spec.predicate.value:<16} {domain} -> {range_}  {flags}")
