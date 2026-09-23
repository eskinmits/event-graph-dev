"""Saving a built projection to disk, so iterating does not mean rebuilding.

A slice that costs minutes to close over in ClickHouse loads from a file in seconds, which
is the difference between exploring the graph and waiting for it. The file is a cache and
never a source of truth (D1) -- it carries the metadata naming the slice, the commit and
the build time, so a stale one is recognisable rather than silently wrong.
"""

import logging
import pickle
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from event_graph.graph.build import BuildMetadata, BuildReport
from event_graph.graph.errors import ConfigurationError
from event_graph.graph.graph import EventGraph

logger = logging.getLogger(__name__)

SAVE_FORMAT_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class SavedGraph:
    """A projection read back from disk, with the provenance it was saved with."""

    graph: EventGraph
    metadata: BuildMetadata
    report: BuildReport
    path: Path

    @property
    def age(self) -> timedelta:
        return datetime.now(UTC) - self.metadata.built_at

    def describe(self) -> str:
        hours = self.age.total_seconds() / 3600
        return (
            f"{self.path} · {self.graph.node_count:,} nodes · {self.graph.edge_count:,} edges"
            f" · built {hours:.1f}h ago from {self.metadata.source}"
        )


def save_graph(
    path: Path,
    graph: EventGraph,
    metadata: BuildMetadata,
    report: BuildReport,
) -> None:
    """Write a built projection and its provenance to `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "format_version": SAVE_FORMAT_VERSION,
        "graph": graph,
        "metadata": metadata,
        "report": report,
    }
    with path.open("wb") as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
    logger.info("saved %d nodes to %s", graph.node_count, path)


def load_graph(path: Path) -> SavedGraph:
    """Read a projection saved by `save_graph`.

    This unpickles, so it will happily execute whatever is in the file. Only load slices
    this repo wrote.
    """
    if not path.exists():
        raise ConfigurationError(
            f"no saved slice at {path}.\n"
            "Build one first, e.g. "
            f"`uv run event-graph graph build --country ES --save {path}`"
        )

    with path.open("rb") as handle:
        payload = pickle.load(handle)

    version = payload.get("format_version") if isinstance(payload, dict) else None
    if version != SAVE_FORMAT_VERSION:
        raise ConfigurationError(
            f"{path} was written in save format {version!r}, this build reads "
            f"{SAVE_FORMAT_VERSION}. Rebuild the slice with `graph build --save {path}`."
        )

    saved = SavedGraph(
        graph=payload["graph"],
        metadata=payload["metadata"],
        report=payload["report"],
        path=path,
    )
    logger.info("loaded %s", saved.describe())
    return saved
