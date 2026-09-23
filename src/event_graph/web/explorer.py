"""The neighbourhood explorer: search for a node, see what surrounds it and why.

This is the demo and the exploration tool at once. It holds one built projection in memory
for the life of the process, so every query after the first is instant -- which is the
whole point, given a market slice costs minutes to close over in ClickHouse.

Deliberately `http.server` rather than a framework: three routes and a static directory do
not justify a dependency, and the projection lives in the process rather than behind one.
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final
from urllib.parse import parse_qs, urlparse

from event_graph.graph.build import BuildMetadata, BuildReport
from event_graph.graph.errors import NodeNotFoundError
from event_graph.graph.graph import Direction, EventGraph
from event_graph.graph.ontology import ENTITY_NODE_TYPES, NodeType

logger = logging.getLogger(__name__)

STATIC_ROOT: Final = Path(__file__).parent / "static"

# a neighbourhood past a few hundred nodes is a hairball nobody can read, so the view is
# capped and says so rather than rendering something meaningless
DEFAULT_NODE_LIMIT: Final = 300

# scanning out of a hub to close the graph back up costs more than the edges are worth
INDUCED_EDGE_MAX_DEGREE: Final = 2_000


@dataclass(frozen=True)
class Explorer:
    """Answers the questions the page asks, against one built projection.

    Not slotted: `meta_payload` caches counts that cost a pass over every node, and
    `cached_property` needs an instance `__dict__` to put them in.
    """

    graph: EventGraph
    metadata: BuildMetadata
    report: BuildReport

    @cached_property
    def meta_payload(self) -> dict[str, Any]:
        return {
            "source": self.metadata.source,
            "slice": self.metadata.slice.describe(),
            "built_at": self.metadata.built_at.isoformat(),
            "git_commit": self.metadata.git_commit,
            "nodes": self.graph.node_count,
            "edges": self.graph.edge_count,
            "node_counts": {
                node_type.value: count
                for node_type, count in sorted(
                    self.graph.node_counts().items(), key=lambda item: -item[1]
                )
            },
            "edge_counts": {
                predicate.value: count
                for predicate, count in sorted(
                    self.graph.edge_counts().items(), key=lambda item: -item[1]
                )
            },
            "node_types": [node_type.value for node_type in NodeType],
        }

    def search(
        self, text: str, node_types: frozenset[NodeType] | None, limit: int
    ) -> dict[str, Any]:
        found = self.graph.search(text, node_types=node_types, limit=limit)
        return {
            "results": [
                {
                    "node_id": node.node_id,
                    "node_type": node.node_type.value,
                    "label": node.label or node.natural_key,
                    "degree": self.graph.degree(node.node_id),
                }
                for node in found
            ]
        }

    def neighbourhood(
        self,
        node_id: str,
        *,
        hops: int,
        direction: Direction,
        max_degree: int,
        through_classifications: bool,
        min_confidence: float,
        limit: int,
    ) -> dict[str, Any]:
        neighbourhood = self.graph.expand(
            node_id,
            hops=hops,
            direction=direction,
            max_degree=max_degree,
            min_confidence=min_confidence,
            through=None if through_classifications else ENTITY_NODE_TYPES,
        )

        # truncation drops a suffix ordered by hop, so every kept node still has the
        # ancestors that explain it -- they sit at a strictly lower hop
        reached = list(neighbourhood)
        kept = reached[:limit]
        kept_ids = {item.node.node_id for item in kept}
        on_path = {edge.edge_id for item in kept for edge in item.path}

        return {
            "root": {
                "node_id": neighbourhood.root.node_id,
                "node_type": neighbourhood.root.node_type.value,
                "label": neighbourhood.root.label or neighbourhood.root.natural_key,
            },
            "nodes": [
                {
                    "node_id": item.node.node_id,
                    "node_type": item.node.node_type.value,
                    "label": item.node.label or item.node.natural_key,
                    "hops": item.hops,
                    "confidence": round(item.confidence, 3),
                    "degree": self.graph.degree(item.node.node_id),
                    "explain": item.explain(),
                }
                for item in kept
            ],
            "edges": [
                {
                    "edge_id": edge.edge_id,
                    "source": edge.src_node_id,
                    "target": edge.dst_node_id,
                    "predicate": edge.predicate.value,
                    "provenance": edge.source,
                    "source_class": edge.source_class.value,
                    "confidence": round(edge.confidence, 3),
                    "inferred": edge.is_inferred,
                    "on_path": edge.edge_id in on_path,
                }
                for edge in self.graph.induced_edges(
                    kept_ids, max_degree=INDUCED_EDGE_MAX_DEGREE
                )
            ],
            "reached": len(reached),
            "truncated": max(0, len(reached) - len(kept)),
        }


class _Handler(BaseHTTPRequestHandler):
    """Routes for the explorer. One instance per request, so it holds no state."""

    explorer: Explorer
    server_version = "event-graph-explorer"

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        route = urlparse(self.path)
        query = parse_qs(route.query)
        try:
            if route.path == "/":
                self._send_static("index.html")
            elif route.path.startswith("/static/"):
                self._send_static(route.path.removeprefix("/static/"))
            elif route.path == "/api/meta":
                self._send_json(self.explorer.meta_payload)
            elif route.path == "/api/search":
                self._send_json(self._search(query))
            elif route.path == "/api/neighbourhood":
                self._send_json(self._neighbourhood(query))
            else:
                self._send_error(HTTPStatus.NOT_FOUND, f"no route {route.path}")
        except NodeNotFoundError as error:
            self._send_error(HTTPStatus.NOT_FOUND, str(error))
        except (ValueError, KeyError) as error:
            self._send_error(HTTPStatus.BAD_REQUEST, str(error))

    def _search(self, query: dict[str, list[str]]) -> dict[str, Any]:
        types = _first(query, "types", "")
        node_types = (
            frozenset(NodeType(value) for value in types.split(",") if value)
            if types
            else None
        )
        return self.explorer.search(
            _first(query, "q", ""),
            node_types=node_types,
            limit=int(_first(query, "limit", "20")),
        )

    def _neighbourhood(self, query: dict[str, list[str]]) -> dict[str, Any]:
        node_id = _first(query, "node", "")
        if not node_id:
            raise ValueError("neighbourhood needs a `node` parameter, e.g. event:abc-123")
        return self.explorer.neighbourhood(
            node_id,
            hops=int(_first(query, "hops", "2")),
            direction=Direction(_first(query, "direction", Direction.BOTH.value)),
            max_degree=int(_first(query, "max_degree", "1000")),
            through_classifications=_first(query, "through_classifications", "0") == "1",
            min_confidence=float(_first(query, "min_confidence", "0")),
            limit=int(_first(query, "limit", str(DEFAULT_NODE_LIMIT))),
        )

    def _send_json(self, payload: dict[str, Any]) -> None:
        self._send_bytes(
            json.dumps(payload).encode(), "application/json; charset=utf-8", HTTPStatus.OK
        )

    def _send_static(self, name: str) -> None:
        path = (STATIC_ROOT / name).resolve()
        if not path.is_file() or STATIC_ROOT.resolve() not in path.parents:
            self._send_error(HTTPStatus.NOT_FOUND, f"no asset {name}")
            return
        self._send_bytes(path.read_bytes(), _CONTENT_TYPES.get(path.suffix, "text/plain"))

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_bytes(
            json.dumps({"error": message}).encode(), "application/json; charset=utf-8", status
        )

    def _send_bytes(
        self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - %s", self.address_string(), format % args)


_CONTENT_TYPES: Final[dict[str, str]] = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


def _first(query: dict[str, list[str]], key: str, default: str) -> str:
    values = query.get(key) or ()
    return values[0] if values else default


def serve(
    explorer: Explorer,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Run the explorer until interrupted. Binds to loopback: this holds internal data."""
    handler = type("BoundHandler", (_Handler,), {"explorer": explorer})
    with ThreadingHTTPServer((host, port), handler) as httpd:
        logger.info("explorer listening on http://%s:%d", host, port)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.info("explorer stopped")


def missing_assets() -> Sequence[str]:
    """Which front-end assets are absent, so the CLI can say so before binding a port."""
    required = ("index.html", "explorer.js", "explorer.css", "cytoscape.min.js")
    return [name for name in required if not (STATIC_ROOT / name).is_file()]
