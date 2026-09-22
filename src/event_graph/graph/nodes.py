from datetime import datetime
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, model_validator

from event_graph.graph.errors import OntologyViolation
from event_graph.graph.ontology import NodeType, parse_node_id


class Node(BaseModel):
    """One row of the node registry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    COLUMNS: ClassVar[tuple[str, ...]] = (
        "node_id",
        "node_type",
        "natural_key",
        "label",
        "data_updated_at",
    )

    node_id: str
    node_type: NodeType
    natural_key: str
    label: str = ""
    data_updated_at: datetime | None = None

    @model_validator(mode="after")
    def _node_id_agrees_with_type(self) -> Self:
        prefix, _ = parse_node_id(self.node_id)
        if prefix is not self.node_type:
            raise OntologyViolation(
                f"node {self.node_id!r} is prefixed {prefix!r} but typed {self.node_type!r}"
            )
        return self

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> Self:
        """Build a node from a ClickHouse row selected in `COLUMNS` order."""
        return cls.model_validate(dict(zip(cls.COLUMNS, row, strict=True)))

    def __str__(self) -> str:
        return f"{self.label or self.natural_key} ({self.node_id})"
