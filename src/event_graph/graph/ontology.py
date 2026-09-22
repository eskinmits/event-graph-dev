import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from event_graph.graph.errors import OntologyViolation

NODE_ID_SEPARATOR: Final = ":"


class NodeType(StrEnum):
    """The ten node types `dim_graph_nodes` holds, verified against the built table.

    Ontology v0 also names Country, EventType and Alias; no rows carry them yet, so they
    are absent here and get added when dbt starts producing them. The User layer is
    modeled in Notion but out of scope for the hackathon.
    """

    EVENT = "event"
    ARTIST = "artist"
    EXTERNAL_EVENT = "external_event"
    VENUE = "venue"
    CITY = "city"
    ORGANIZER_BRAND = "organizer_brand"
    SERIES = "series"
    ORGANIZER_COMPANY = "organizer_company"
    TICKET_PROVIDER = "ticket_provider"
    GENRE = "genre"


class Predicate(StrEnum):
    """Edge predicates. The eleven sourced ones are built; `conflicts_with` is derived.

    Domain and range below were checked against the 19.29M rows actually in the table.
    """

    PERFORMS_AT = "performs_at"
    HELD_AT = "held_at"
    IN_CITY = "in_city"
    PROMOTED_BY = "promoted_by"
    OWNED_BY = "owned_by"
    SOLD_VIA = "sold_via"
    IMPORTED_AS = "imported_as"
    HAS_GENRE = "has_genre"
    SERIES_OF = "series_of"
    SIMILAR_TO = "similar_to"
    SAME_AS = "same_as"
    CONFLICTS_WITH = "conflicts_with"


class SourceClass(StrEnum):
    """The grouped provenance enum. The raw upstream value stays in `Edge.source`.

    The first six are produced by dbt. `graph_inferred` is ours alone -- it never appears
    in `bridge_graph_edges_source`, only in `dbt_graph.graph_edges_inferred`.
    """

    ADMIN_VERIFIED = "admin_verified"
    SELLER_INPUT = "seller_input"
    EVENT_ENGINE_IMPORT = "event_engine_import"
    SYSTEM_RULE = "system_rule"
    THIRD_PARTY = "third_party"
    SYSTEM_OF_RECORD = "system_of_record"
    GRAPH_INFERRED = "graph_inferred"


@dataclass(frozen=True, slots=True)
class PredicateSpec:
    """What a predicate may connect, and whether we produced it ourselves.

    `symmetric` predicates are stored as a single directed edge and traversed both ways;
    their endpoints must share a node type. `derived` marks a predicate no upstream system
    supplies -- it only ever exists because we computed it.
    """

    predicate: Predicate
    domain: frozenset[NodeType]
    range: frozenset[NodeType]
    symmetric: bool = False
    derived: bool = False


def _spec(
    predicate: Predicate,
    domain: set[NodeType],
    range_: set[NodeType],
    *,
    symmetric: bool = False,
    derived: bool = False,
) -> PredicateSpec:
    return PredicateSpec(predicate, frozenset(domain), frozenset(range_), symmetric, derived)


ONTOLOGY: Final[dict[Predicate, PredicateSpec]] = {
    spec.predicate: spec
    for spec in (
        _spec(Predicate.PERFORMS_AT, {NodeType.ARTIST}, {NodeType.EVENT}),
        _spec(Predicate.HELD_AT, {NodeType.EVENT}, {NodeType.VENUE}),
        _spec(Predicate.IN_CITY, {NodeType.VENUE}, {NodeType.CITY}),
        _spec(Predicate.PROMOTED_BY, {NodeType.EVENT}, {NodeType.ORGANIZER_BRAND}),
        _spec(Predicate.OWNED_BY, {NodeType.ORGANIZER_BRAND}, {NodeType.ORGANIZER_COMPANY}),
        _spec(Predicate.SOLD_VIA, {NodeType.EVENT}, {NodeType.TICKET_PROVIDER}),
        _spec(Predicate.IMPORTED_AS, {NodeType.EXTERNAL_EVENT}, {NodeType.EVENT}),
        _spec(Predicate.SERIES_OF, {NodeType.EVENT}, {NodeType.SERIES}),
        _spec(
            Predicate.HAS_GENRE,
            {NodeType.ARTIST, NodeType.EVENT, NodeType.VENUE},
            {NodeType.GENRE},
        ),
        _spec(Predicate.SIMILAR_TO, {NodeType.ARTIST}, {NodeType.ARTIST}, symmetric=True),
        _spec(
            Predicate.SAME_AS,
            {NodeType.EVENT, NodeType.VENUE, NodeType.ARTIST},
            {NodeType.EVENT, NodeType.VENUE, NodeType.ARTIST},
            symmetric=True,
        ),
        _spec(
            Predicate.CONFLICTS_WITH,
            {NodeType.EVENT},
            {NodeType.EVENT},
            symmetric=True,
            derived=True,
        ),
    )
}

SOURCED_PREDICATES: Final = frozenset(p for p, s in ONTOLOGY.items() if not s.derived)

# the types a walk may pass through. the rest are classifications -- a genre or a ticket
# provider is a label shared by millions of events, so arriving at one tells you nothing
# about what else it touches
ENTITY_NODE_TYPES: Final[frozenset[NodeType]] = frozenset(
    {
        NodeType.EVENT,
        NodeType.ARTIST,
        NodeType.VENUE,
        NodeType.EXTERNAL_EVENT,
        NodeType.ORGANIZER_BRAND,
        NodeType.SERIES,
    }
)


def validate_endpoints(predicate: Predicate, src_type: NodeType, dst_type: NodeType) -> None:
    """Raise if an edge connects node types the ontology does not allow it to connect."""
    spec = ONTOLOGY[predicate]
    if src_type not in spec.domain or dst_type not in spec.range:
        raise OntologyViolation(
            f"{predicate} may not connect {src_type} -> {dst_type}; "
            f"it connects {'|'.join(sorted(spec.domain))} -> {'|'.join(sorted(spec.range))}"
        )
    if spec.symmetric and src_type != dst_type:
        raise OntologyViolation(
            f"{predicate} is symmetric and needs both endpoints on the same node type, "
            f"got {src_type} -> {dst_type}"
        )


def format_node_id(node_type: NodeType, natural_key: str) -> str:
    """Build the canonical node id, e.g. `artist:xyz`."""
    return f"{node_type.value}{NODE_ID_SEPARATOR}{natural_key}"


def parse_node_id(node_id: str) -> tuple[NodeType, str]:
    """Split a canonical node id back into its type and natural key."""
    prefix, separator, natural_key = node_id.partition(NODE_ID_SEPARATOR)
    if not separator or not natural_key:
        raise OntologyViolation(
            f"node id {node_id!r} is not of the form '<node_type>{NODE_ID_SEPARATOR}<natural_key>'"
        )
    try:
        return NodeType(prefix), natural_key
    except ValueError:
        raise OntologyViolation(
            f"node id {node_id!r} has unknown node type {prefix!r}; "
            f"known types are {', '.join(sorted(NodeType))}"
        ) from None


def deterministic_edge_id(
    src_node_id: str, predicate: Predicate, dst_node_id: str, source: str
) -> str:
    """Hash the identity tuple of an edge, so re-running a run produces the same rows.

    dbt hashes the same four fields with `sipHash128` for source edges. The digests differ,
    which is harmless: `source` is part of the tuple and inferred edges always carry
    `graph_inferred`, so the two writers can never mint an id for the same edge.
    """
    payload = "\x1f".join((src_node_id, predicate.value, dst_node_id, source))
    return hashlib.blake2b(payload.encode(), digest_size=16).hexdigest()
