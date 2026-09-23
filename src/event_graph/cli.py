import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from event_graph.clickhouse import get_client as get_clickhouse_client
from event_graph.config import get_config
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
from event_graph.graph.store import load_graph, save_graph
from event_graph.web import Explorer, missing_assets, serve
from event_graph.web.inferences import InferredOverlay


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
                type=click.Choice([p.value for p in Predicate]),  # noqa
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


def load_option[F: Callable[..., None]](command: F) -> F:
    """Read a slice built earlier instead of querying ClickHouse."""
    return click.option(
        "--load",
        "load",
        default=None,
        type=click.Path(dir_okay=False, path_type=Path),
        help="Load a slice saved by `graph build --save` instead of rebuilding.",
    )(command)


def _projection(
    load: Path | None,
    country: str | None,
    predicates: tuple[str, ...],
    min_confidence: float,
    max_edges: int | None,
    keep_junk_hubs: bool,
    prune_above_degree: int | None,
    lenient: bool,
) -> tuple[EventGraph, BuildMetadata, BuildReport]:
    if load is not None:
        if any(
            (
                country,
                predicates,
                min_confidence,
                max_edges,
                keep_junk_hubs,
                prune_above_degree,
                lenient,
            )
        ):
            raise click.UsageError(
                "--load reads a slice that was already built, so the slice flags cannot "
                "change it. Drop them, or rebuild with `graph build --save`."
            )
        saved = load_graph(load)
        click.echo(f"loaded {saved.describe()}", err=True)
        return saved.graph, saved.metadata, saved.report

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
@click.option(
    "--save",
    default=None,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the built slice here so later commands can skip the rebuild.",
)
@slice_options
def build_graph(save: Path | None, **options: Any) -> None:
    """Build the projection and report what it contains."""
    projection, metadata, report = _projection(load=None, **options)

    click.echo(f"Source:    {metadata.source}")
    click.echo(f"Built at:  {metadata.built_at:%Y-%m-%d %H:%M:%S %Z} (commit {metadata.git_commit})")  # noqa
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

    if save is not None:
        save_graph(save, projection, metadata, report)
        click.echo(f"\nSaved to {save} · reload it with --load {save}")


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
    type=click.Choice([d.value for d in Direction]),  # noqa
)
@click.option(
    "--through-classifications",
    is_flag=True,
    help="Also walk through genre and provider nodes, which connect unrelated events.",
)
@load_option
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
    projection, _, _ = _projection(**options)

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


@graph.command("create-inferred-table")
def create_inferred() -> None:
    """Create the table inference writes back to, if it does not exist yet.

    Safe to rerun. The table name comes from GRAPH_INFERRED_TABLE (see config.py).
    """
    from event_graph.graph.inferred import create_inferred_table

    table = get_config().graph.inferred_table
    created = create_inferred_table(get_clickhouse_client(), table)
    click.echo(f"{'Created' if created else 'Already exists:'} {table}")


@graph.command("serve")
@click.option("--host", default="127.0.0.1", help="Interface to bind. Loopback by default.")
@click.option("--port", default=8000, type=int, help="Port to listen on.")
@click.option(
    "--inferred",
    "inferred_path",
    default=None,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Show inferred edges from a JSONL file written by bin/lineup_linking.py.",
)
@click.option(
    "--inferred-run",
    default=None,
    help="Show one run read back from the inferred-edges table in ClickHouse.",
)
@load_option
@slice_options
def serve_explorer(
    host: str,
    port: int,
    inferred_path: Path | None,
    inferred_run: str | None,
    **options: Any,
) -> None:
    """Explore the projection in a browser: search a node, see its neighbourhood and why.

    The slice is held in memory for the life of the process, so pass --load unless you
    want to wait for a rebuild first. Inferred edges are shown beside the projection, never
    loaded into it.
    """
    if inferred_path and inferred_run:
        raise click.UsageError("pass --inferred or --inferred-run, not both.")
    absent = missing_assets()
    if absent:
        raise click.UsageError(
            f"the explorer's front-end is missing {', '.join(absent)}. "
            "Expected them in src/event_graph/web/static/."
        )

    inferred: InferredOverlay | None = None
    if inferred_path:
        inferred = InferredOverlay.from_jsonl(inferred_path)
    elif inferred_run:
        inferred = InferredOverlay.from_clickhouse(
            get_clickhouse_client(), get_config().graph.inferred_table, inferred_run
        )

    projection, metadata, report = _projection(**options)
    click.echo(f"Serving {projection.node_count:,} nodes on http://{host}:{port}")
    if inferred:
        click.echo(f"with {inferred.description}")
    click.echo("Ctrl-C to stop.")
    serve(Explorer(projection, metadata, report, inferred), host=host, port=port)
