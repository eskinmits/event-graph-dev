"""Inferred edges held beside the projection, for looking at -- never inside it.

The projection is built from source edges only, so a run's guesses cannot become the next
run's evidence. The explorer still has to show them, so they live here, keyed for the two
questions the page asks: what did a run propose, and what touches this node.
"""

from collections import Counter, defaultdict
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Any, Self

from clickhouse_connect.driver.client import Client
from pydantic import ValidationError

from event_graph.graph.edges import Edge
from event_graph.graph.errors import ConfigurationError
from event_graph.graph.source import _identifier


class InferredOverlay:
    def __init__(self, edges: Iterable[Edge], description: str) -> None:
        self.edges: list[Edge] = sorted(edges, key=lambda e: (-e.confidence, e.edge_id))
        self.description = description
        self.by_id: dict[str, Edge] = {edge.edge_id: edge for edge in self.edges}
        self._by_node: dict[str, list[Edge]] = defaultdict(list)
        for edge in self.edges:
            self._by_node[edge.src_node_id].append(edge)
            self._by_node[edge.dst_node_id].append(edge)

    @classmethod
    def from_jsonl(cls, path: Path) -> Self:
        """Read the rows `bin/lineup_linking.py` writes next to its worklist."""
        if not path.is_file():
            raise ConfigurationError(
                f"no inferred edges at {path}.\n"
                "Produce them with `uv run python bin/lineup_linking.py --country NL "
                "--slice slices/nl.pkl`."
            )
        try:
            edges = [
                Edge.model_validate_json(line)
                for line in path.read_text().splitlines()
                if line.strip()
            ]
        except ValidationError as error:
            raise ConfigurationError(f"{path} does not hold inferred edge rows: {error}") from error
        return cls(edges, f"{path} ({len(edges):,} edges)")

    @classmethod
    def from_clickhouse(cls, client: Client, table: str, run_id: str) -> Self:
        """Read one run back from the inferred-edges table, as every other consumer would."""
        columns = ", ".join(Edge.COLUMNS)
        rows = client.query(
            f"SELECT {columns} FROM {_identifier(table, 'inferred_table')} FINAL"
            " WHERE run_id = {run_id:String}",
            parameters={"run_id": run_id},
        ).result_rows
        if not rows:
            raise ConfigurationError(
                f"run {run_id!r} has no rows in {table}. "
                f"List the runs with `SELECT run_id, count() FROM {table} GROUP BY run_id`."
            )
        return cls((Edge.from_row(tuple(row)) for row in rows), f"{table} run {run_id} ({len(rows):,} edges)")

    def touching(self, node_ids: Collection[str]) -> list[Edge]:
        seen: dict[str, Edge] = {}
        for node_id in node_ids:
            for edge in self._by_node.get(node_id, ()):
                seen[edge.edge_id] = edge
        return list(seen.values())

    def of_node(self, node_id: str) -> list[Edge]:
        return list(self._by_node.get(node_id, ()))

    def summary(self) -> dict[str, Any]:
        rules: dict[str, dict[str, Any]] = {}
        for edge in self.edges:
            evidence = edge.evidence or {}
            arm = str(evidence.get("arm", "unknown"))
            rule = rules.setdefault(
                arm,
                {
                    "proposals": 0,
                    "precision": evidence.get("rule_precision"),
                    "calibrated_on": evidence.get("calibrated_on"),
                },
            )
            rule["proposals"] += 1
        return {
            "description": self.description,
            "proposals": len(self.edges),
            "events": len({edge.dst_node_id for edge in self.edges}),
            "artists": len({edge.src_node_id for edge in self.edges}),
            "runs": dict(Counter(edge.run_id or "" for edge in self.edges)),
            "rules": rules,
        }
