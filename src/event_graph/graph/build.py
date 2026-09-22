"""Building the projection from a source, and the record of how it was built.

Every number this repo reports has to be regenerable from a command, so a built graph
carries the slice, the timestamp and the commit that produced it -- and a report of
everything the build had to throw away.
"""

import logging
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from event_graph.graph.errors import EmptyGraphError, GraphError, UnknownNodeError
from event_graph.graph.filters import DEFAULT_NODE_FILTER, NodeFilter
from event_graph.graph.graph import EventGraph
from event_graph.graph.ontology import NodeType
from event_graph.graph.source import GraphSlice, GraphSource

logger = logging.getLogger(__name__)


def _git_commit() -> str | None:
    """Best-effort commit of the working tree, for reproducing a result later."""
    try:
        result = subprocess.run(
            ("git", "rev-parse", "--short", "HEAD"),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except OSError:
        return None
    return result.stdout.strip() or None


@dataclass(frozen=True, slots=True)
class BuildMetadata:
    """What this projection is, and nothing else -- it holds no state of its own."""

    source: str
    slice: GraphSlice
    built_at: datetime
    git_commit: str | None


@dataclass(slots=True)
class BuildReport:
    """What the build kept and what it dropped.

    Skipped rows are counted rather than silenced: dangling edges are a known property of
    the built tables (~2.8k point at rows the dims exclude), so a run that suddenly drops
    far more of them is telling you something upstream broke.
    """

    nodes_loaded: int = 0
    edges_loaded: int = 0
    nodes_filtered: int = 0
    edges_dangling: int = 0
    edges_off_ontology: int = 0
    hubs_pruned: int = 0
    duration_seconds: float = 0.0
    dropped_node_types: dict[NodeType, int] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"nodes {self.nodes_loaded:,} · edges {self.edges_loaded:,} "
            f"· {self.duration_seconds:.1f}s",
        ]
        dropped = {
            "filtered nodes": self.nodes_filtered,
            "dangling edges": self.edges_dangling,
            "off-ontology edges": self.edges_off_ontology,
            "pruned hubs": self.hubs_pruned,
        }
        for label, count in dropped.items():
            if count:
                lines.append(f"  dropped {count:,} {label}")
        return "\n".join(lines)


class GraphBuilder:
    """Turns a source into an `EventGraph`.

    Junk hubs are filtered by default; pass `node_filter=keep_everything` to see the
    graph exactly as ClickHouse holds it.

    `strict` decides what an edge that breaks the ontology means -- an edge connecting node
    types its predicate is not allowed to connect. Strict is right once the vocabulary is
    settled; lenient is right when a dbt change is mid-flight and you want the build to
    finish and the report to tell you how much it had to drop.
    """

    def __init__(
        self,
        source: GraphSource,
        *,
        node_filter: NodeFilter = DEFAULT_NODE_FILTER,
        strict: bool = True,
        max_hub_degree: int | None = None,
    ) -> None:
        self.source = source
        self.node_filter = node_filter
        self.strict = strict
        self.max_hub_degree = max_hub_degree

    def build(self) -> tuple[EventGraph, BuildMetadata, BuildReport]:
        """Load nodes, then edges, then prune. Returns the graph and how it was made."""
        started = time.monotonic()
        graph = EventGraph()
        report = BuildReport()

        logger.info("loading nodes from %s", self.source.description)
        for node in self.source.nodes():
            if not self.node_filter(node):
                report.nodes_filtered += 1
                continue
            graph.add_node(node)
            report.nodes_loaded += 1

        if not report.nodes_loaded:
            raise EmptyGraphError(
                f"no nodes came back from {self.source.description}. "
                "Check the slice is not empty and that the tunnel points at the right service."
            )

        logger.info("loaded %d nodes, loading edges", report.nodes_loaded)
        for edge in self.source.edges():
            try:
                graph.add_edge(edge)
            except UnknownNodeError:
                report.edges_dangling += 1
                continue
            except GraphError:
                report.edges_off_ontology += 1
                if self.strict:
                    raise
                continue
            report.edges_loaded += 1

        if self.max_hub_degree is not None:
            report.hubs_pruned = len(graph.prune_hubs(max_degree=self.max_hub_degree))

        report.duration_seconds = time.monotonic() - started
        metadata = BuildMetadata(
            source=self.source.description,
            slice=self.source.slice,
            built_at=datetime.now(UTC),
            git_commit=_git_commit(),
        )
        logger.info(
            "built graph: %d nodes, %d edges in %.1fs",
            report.nodes_loaded,
            report.edges_loaded,
            report.duration_seconds,
        )
        return graph, metadata, report
