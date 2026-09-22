"""Node filters applied while the projection is built.

Junk hubs are the trap this exists for: one wrongly-merged hub fuses every cluster it
touches, the same failure mode as the `DELETED_EVENT_ID` sink in `ml-event-deduplication`.
They have to go before clustering, not after.
"""

from collections.abc import Callable, Iterable
from typing import Final

from event_graph.graph.nodes import Node
from event_graph.graph.ontology import NodeType

type NodeFilter = Callable[[Node], bool]

DEFAULT_JUNK_HUB_LABELS: Final[frozenset[str]] = frozenset(
    {
        "unbekannt",
        "auditorium",
        "saturday night",
    }
)

# only entity nodes fuse clusters; 278 events are genuinely called "Saturday Night" and
# dropping them would delete real rows rather than a hub
DEFAULT_JUNK_HUB_NODE_TYPES: Final[frozenset[NodeType]] = frozenset(
    {NodeType.ARTIST, NodeType.VENUE}
)


def junk_hub_filter(
    labels: Iterable[str] = DEFAULT_JUNK_HUB_LABELS,
    node_types: Iterable[NodeType] = DEFAULT_JUNK_HUB_NODE_TYPES,
) -> NodeFilter:
    """Reject nodes whose label is a known junk hub, case-folded.

    The seed list is short and hand-checked. Growing it from data (entity nodes whose
    degree is orders of magnitude above their type's median) is a later job.
    """
    folded = frozenset(label.casefold().strip() for label in labels)
    types = frozenset(node_types)

    def keep(node: Node) -> bool:
        return not (node.node_type in types and node.label.casefold().strip() in folded)

    return keep


def node_type_filter(node_types: Iterable[NodeType]) -> NodeFilter:
    """Keep only the given node types."""
    types = frozenset(node_types)

    def keep(node: Node) -> bool:
        return node.node_type in types

    return keep


def keep_everything(node: Node) -> bool:
    """Keep every node. Pass this to opt out of junk-hub filtering, visibly."""
    return True


def all_of(*filters: NodeFilter) -> NodeFilter:
    """Combine filters; a node is kept when every filter keeps it."""

    def keep(node: Node) -> bool:
        return all(f(node) for f in filters)

    return keep


# filtering junk hubs is the default because the failure is silent and asymmetric: keeping
# them fuses unrelated clusters, while dropping them costs four entity rows
DEFAULT_NODE_FILTER: Final[NodeFilter] = junk_hub_filter()
