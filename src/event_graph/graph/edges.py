import json
from datetime import UTC, datetime
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from event_graph.graph.ontology import Predicate, SourceClass, deterministic_edge_id


class Edge(BaseModel):
    """One row of the edge table, from either writer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    COLUMNS: ClassVar[tuple[str, ...]] = (
        "edge_id",
        "src_node_id",
        "predicate",
        "dst_node_id",
        "source",
        "source_class",
        "confidence",
        "observed_at",
        "run_id",
        "evidence",
    )

    edge_id: str
    src_node_id: str
    predicate: Predicate
    dst_node_id: str
    source: str
    source_class: SourceClass
    confidence: float = Field(ge=0.0, le=1.0)
    observed_at: datetime | None = None
    run_id: str | None = None
    evidence: dict[str, Any] | None = None

    @field_validator("run_id", mode="before")
    @classmethod
    def _blank_run_id_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("evidence", mode="before")
    @classmethod
    def _parse_evidence(cls, value: object) -> object:
        """ClickHouse holds evidence as a JSON string; empty means there is none."""
        if isinstance(value, str):
            return json.loads(value) if value.strip() else None
        return value

    @property
    def is_inferred(self) -> bool:
        return self.source_class is SourceClass.GRAPH_INFERRED

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> Self:
        """Build an edge from a ClickHouse row selected in `COLUMNS` order."""
        return cls.model_validate(dict(zip(cls.COLUMNS, row, strict=True)))

    @classmethod
    def inferred(
        cls,
        src_node_id: str,
        predicate: Predicate,
        dst_node_id: str,
        *,
        confidence: float,
        run_id: str,
        evidence: dict[str, Any],
        observed_at: datetime | None = None,
    ) -> Self:
        """Mint an edge this run proposes, addressed to `dbt_graph.graph_edges_inferred`.

        `evidence` is not optional here on purpose: an inferred edge nobody can explain is
        not something we are willing to write.
        """
        source = SourceClass.GRAPH_INFERRED.value
        return cls(
            edge_id=deterministic_edge_id(src_node_id, predicate, dst_node_id, source),
            src_node_id=src_node_id,
            predicate=predicate,
            dst_node_id=dst_node_id,
            source=source,
            source_class=SourceClass.GRAPH_INFERRED,
            confidence=confidence,
            observed_at=observed_at or datetime.now(UTC),
            run_id=run_id,
            evidence=evidence,
        )

    def __str__(self) -> str:
        return (
            f"{self.src_node_id} -[{self.predicate} "
            f"{self.source}@{self.confidence:.2f}]-> {self.dst_node_id}"
        )
