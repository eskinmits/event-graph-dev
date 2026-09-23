"""Link artist-less upcoming events to artists from the lineup names Event Engine providers sent.

Evaluates every rule on events whose artist is already known, calibrates each rule's
confidence from that, then proposes links for the artist-less upcoming events -- ranked,
explained, and above a precision floor. `--write` inserts them into the inferred-edges
table under one `run_id`, so a bad run is one delete.

    uv run event-graph graph build --country NL --save slices/nl.pkl   # once
    uv run python bin/lineup_linking.py --country NL --slice slices/nl.pkl
    uv run python bin/lineup_linking.py --country NL --slice slices/nl.pkl --write
"""

import csv
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import click

from event_graph.clickhouse import get_client
from event_graph.config import get_config
from event_graph.graph.store import load_graph
from event_graph.linking.evaluate import evaluate, report
from event_graph.linking.linker import Linker
from event_graph.linking.mentions import load_mentions
from event_graph.linking.neighbourhood import Neighbourhood
from event_graph.linking.proposals import DEFAULT_PRECISION_FLOOR, Proposal, propose
from event_graph.linking.ranker import GraphRanker

INSERT_COLUMNS = (
    "edge_id", "src_node_id", "dst_node_id", "predicate", "source", "source_class",
    "run_id", "evidence", "confidence", "observed_at",
)


def write_csv(path: Path, proposals: list[Proposal]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ("confidence", "event_node_id", "event_title", "artist_node_id", "artist", "why")
        )
        for p in proposals:
            writer.writerow(
                (
                    f"{p.edge.confidence:.3f}", p.edge.dst_node_id, p.decision.mention.title,
                    p.edge.src_node_id, p.artist_label, p.explain(),
                )
            )


def write_jsonl(path: Path, proposals: list[Proposal]) -> None:
    """The rows exactly as the inferred-edges table holds them, for the explorer to load."""
    with path.open("w") as handle:
        for p in proposals:
            handle.write(p.edge.model_dump_json() + "\n")


def insert(proposals: list[Proposal], table: str) -> None:
    rows = [
        [
            p.edge.edge_id, p.edge.src_node_id, p.edge.dst_node_id, p.edge.predicate.value,
            p.edge.source, p.edge.source_class.value, p.edge.run_id,
            json.dumps(p.edge.evidence, ensure_ascii=False), p.edge.confidence,
            (p.edge.observed_at or datetime.now(UTC)).replace(tzinfo=None, microsecond=0),
        ]
        for p in proposals
    ]
    get_client().insert(table, rows, column_names=INSERT_COLUMNS)


@click.command()
@click.option("--country", required=True, help="ISO-2 market, e.g. NL.")
@click.option("--slice", "slice_path", required=True, type=click.Path(path_type=Path))
@click.option("--floor", default=DEFAULT_PRECISION_FLOOR, type=float, help="Precision floor.")
@click.option("--out", default=None, type=click.Path(path_type=Path), help="Worklist CSV.")
@click.option("--write", is_flag=True, help="Insert proposals into the inferred-edges table.")
@click.option(
    "--exact-only",
    is_flag=True,
    help="Skip looking for near-spellings among the event's graph neighbours.",
)
def main(
    country: str, slice_path: Path, floor: float, out: Path | None, write: bool, exact_only: bool
) -> None:
    tables = get_config().graph
    country = country.upper()
    command = " ".join(["uv run python bin/lineup_linking.py", *sys.argv[1:]])

    mentions = load_mentions(get_client(), tables, country)
    click.echo(f"{len(mentions):,} provider lineup names on live {country} events", err=True)

    saved = load_graph(slice_path)
    if saved.metadata.slice.country and saved.metadata.slice.country.upper() != country:
        raise click.UsageError(
            f"{slice_path} was built for {saved.metadata.slice.country}, not {country}."
        )
    click.echo(f"slice {saved.describe()}", err=True)

    neighbourhood = None if exact_only else Neighbourhood(saved.graph)
    decisions = list(Linker(GraphRanker(saved.graph), neighbourhood).decide_all(mentions))
    evaluation = evaluate(decisions)
    click.echo(f"\n== eval, {country}\n{report(evaluation)}")

    version = "v1" if exact_only else "v2"
    run_id = f"{datetime.now(UTC):%Y-%m-%dT%H:%M}_{country.lower()}_lineup_{version}"
    proposals, dropped = propose(
        decisions, evaluation, run_id=run_id, eval_command=command, precision_floor=floor
    )
    targets = {d.mention.event_node_id for d in decisions if d.mention.is_artistless and d.mention.is_upcoming}
    events = {p.edge.dst_node_id for p in proposals}
    arms = Counter(p.edge.evidence["arm"] for p in proposals if p.edge.evidence)

    click.echo(f"\n== proposals, {country}, floor {floor:.0%}, run {run_id}")
    click.echo(
        f"  {len(proposals):,} links on {len(events):,} of {len(targets):,} artist-less "
        f"upcoming events that carry lineup names"
    )
    for arm, n in arms.most_common():
        click.echo(f"    {arm:<18} {n:>6,}")
    click.echo("  dropped")
    for reason, n in sorted(dropped.items(), key=lambda item: -item[1]):
        click.echo(f"    {reason:<48} {n:>6,}")
    click.echo("\n  sample")
    for p in proposals[:: max(1, len(proposals) // 12)][:12]:
        click.echo(f"    {p.edge.confidence:.2f}  {p.decision.mention.title[:48]:<48} -> {p.artist_label}")
        click.echo(f"          {p.explain()}")

    out = out or Path("out") / f"lineup_proposals_{country.lower()}.csv"
    write_csv(out, proposals)
    rows = out.with_suffix(".jsonl")
    write_jsonl(rows, proposals)
    click.echo(f"\nworklist: {out}")
    click.echo(f"edges:    {rows}  ->  uv run event-graph graph serve --load {slice_path} --inferred {rows}")

    if write:
        insert(proposals, tables.inferred_table)
        click.echo(f"inserted {len(proposals):,} rows into {tables.inferred_table} as run {run_id}")
        click.echo(f"rollback: ALTER TABLE {tables.inferred_table} DELETE WHERE run_id = '{run_id}'")


if __name__ == "__main__":
    main()
