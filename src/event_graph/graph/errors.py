
class GraphError(Exception):
    """Base class for every error raised by the graph layer."""


class ConfigurationError(GraphError):
    """Configuration is missing or unusable."""


class OntologyViolation(GraphError):
    """A node or edge does not fit the vocabulary in `ontology.py`."""


class UnknownNodeError(GraphError):
    """An edge references a node the graph does not hold."""


class NodeNotFoundError(GraphError):
    """A lookup named a node that is not in the graph."""


class EmptyGraphError(GraphError):
    """A build produced no nodes, so every downstream number would be meaningless."""
