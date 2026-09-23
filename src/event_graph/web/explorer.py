"""The neighbourhood explorer: search for a node, see what surrounds it and why.

This is the demo and the exploration tool at once. It holds one built projection in memory
for the life of the process, so every query after the first is instant -- which is the
whole point, given a market slice costs minutes to close over in ClickHouse.

Deliberately `http.server` rather than a framework: three routes and a static directory do
not justify a dependency, and the projection lives in the process rather than behind one.
"""

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final
from urllib.parse import parse_qs, urlparse

from event_graph.graph.build import BuildMetadata, BuildReport
from event_graph.graph.edges import Edge
from event_graph.graph.errors import NodeNotFoundError
from event_graph.graph.graph import Direction, EventGraph
from event_graph.graph.ontology import ENTITY_NODE_TYPES, NodeType, Predicate, parse_node_id
from event_graph.linking.neighbourhood import Neighbourhood
from event_graph.linking.ranker import GraphRanker
from event_graph.web.inferences import InferredOverlay

logger = logging.getLogger(__name__)

STATIC_ROOT: Final = Path(__file__).parent / "static"

# a neighbourhood past a few hundred nodes is a hairball nobody can read, so the view is
# capped and says so rather than rendering something meaningless
DEFAULT_NODE_LIMIT: Final = 300

# scanning out of a hub to close the graph back up costs more than the edges are worth
INDUCED_EDGE_MAX_DEGREE: Final = 2_000

# festivals link every act on the bill, so ranking "the" artist first means little there
MAX_BILL_FOR_EXAMPLE: Final = 4


@dataclass(frozen=True)
class Explorer:
    """Answers the questions the page asks, against one built projection.

    Not slotted: `meta_payload` caches counts that cost a pass over every node, and
    `cached_property` needs an instance `__dict__` to put them in.
    """

    graph: EventGraph
    metadata: BuildMetadata
    report: BuildReport
    inferred: InferredOverlay | None = None

    @cached_property
    def ranker(self) -> GraphRanker:
        return GraphRanker(self.graph)

    @cached_property
    def walk_index(self) -> Neighbourhood:
        """The walk index, built on first use: ~10s on a market slice.

        Duplicate listings are left out. A redirected copy of an event still carries its
        artist, so walking `same_as` finds the answer rather than predicting it -- and it
        was the whole of PageRank's lift over venue history when measured with it.
        """
        logger.info("building the walk index")
        return Neighbourhood(self.graph)

    @cached_property
    def meta_payload(self) -> dict[str, Any]:
        return {
            "inferred": self.inferred.summary() if self.inferred else None,
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
        hops_of = {item.node.node_id: item.hops for item in kept}

        inferred = self.inferred.touching(kept_ids) if self.inferred else []
        proposed_nodes: list[dict[str, Any]] = []
        for edge in inferred:
            for end, other in ((edge.src_node_id, edge.dst_node_id), (edge.dst_node_id, edge.src_node_id)):
                if end not in kept_ids and other in kept_ids:
                    kept_ids.add(end)
                    proposed_nodes.append(
                        {
                            **self._node_payload(end, edge),
                            "hops": hops_of[other] + 1,
                            "confidence": round(edge.confidence, 3),
                            "explain": f"proposed by inference: {edge}",
                        }
                    )

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
            ]
            + proposed_nodes,
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
            ]
            + [_edge_payload(edge) for edge in inferred],
            "reached": len(reached),
            "truncated": max(0, len(reached) - len(kept)),
        }


    def inferences(self, text: str, arm: str, limit: int, offset: int) -> dict[str, Any]:
        """The worklist: every proposal, filtered, strongest first."""
        if self.inferred is None:
            return {"total": 0, "matched": 0, "rows": []}
        needle = text.casefold().strip()
        rows = []
        for edge in self.inferred.edges:
            evidence = edge.evidence or {}
            if arm and evidence.get("arm") != arm:
                continue
            event_title = str(evidence.get("event_title") or self._label(edge.dst_node_id))
            artist = str(evidence.get("artist_label") or self._label(edge.src_node_id))
            if needle and needle not in event_title.casefold() and needle not in artist.casefold():
                continue
            rows.append(
                {
                    "edge_id": edge.edge_id,
                    "confidence": round(edge.confidence, 3),
                    "event_node_id": edge.dst_node_id,
                    "event_title": event_title,
                    "artist_node_id": edge.src_node_id,
                    "artist_label": artist,
                    "arm": evidence.get("arm"),
                    "signals": evidence.get("graph_signals") or {},
                    "same_name_rows": evidence.get("same_name_rows", 1),
                    "tribute_token": evidence.get("tribute_token"),
                    "in_slice": edge.dst_node_id in self.graph,
                }
            )
        return {
            "total": len(self.inferred.edges),
            "matched": len(rows),
            "rows": rows[offset : offset + limit],
        }

    def inference(self, edge_id: str) -> dict[str, Any]:
        """One proposal and the subgraph that argues for it, laid out as evidence."""
        if self.inferred is None or edge_id not in self.inferred.by_id:
            raise NodeNotFoundError(f"no inferred edge {edge_id!r} is loaded")
        proposal = self.inferred.by_id[edge_id]
        evidence = proposal.evidence or {}
        event_id, artist_id = proposal.dst_node_id, proposal.src_node_id

        roles: dict[str, str] = {event_id: "target", artist_id: "proposed"}
        edges: dict[str, dict[str, Any]] = {proposal.edge_id: _edge_payload(proposal)}
        signals: dict[str, list[list[str]]] = {}

        external = f"external_event:{evidence.get('external_event_id')}"
        if external in self.graph:
            roles[external] = "provider"
            for edge, _ in self.graph.incident(external, direction=Direction.OUT):
                if edge.dst_node_id == event_id:
                    edges[edge.edge_id] = {
                        **_edge_payload(edge),
                        "signal": "provider",
                        "label": f"lists {evidence.get('provider_name')!r}",
                    }

        if event_id in self.graph:
            cobilled = frozenset(str(a) for a in evidence.get("cobilled") or ())
            context = self.ranker.context(event_id, cobilled)
            for signal, paths in self.ranker.support(context, artist_id).items():
                signals[signal] = []
                for path in paths:
                    signals[signal].append([edge.edge_id for edge in path])
                    for edge in path:
                        edges.setdefault(edge.edge_id, {**_edge_payload(edge), "signal": signal})
                        for end in (edge.src_node_id, edge.dst_node_id):
                            roles.setdefault(end, "evidence")
            for other in cobilled:
                roles.setdefault(other, "cobilled")

        rivals: list[dict[str, Any]] = []
        for candidate in evidence.get("candidates") or ():
            rival_id = str(candidate["artist_node_id"])
            if rival_id == artist_id:
                continue
            roles.setdefault(rival_id, "rival")
            rivals.append(candidate)
            edges[f"rival:{rival_id}"] = {
                "edge_id": f"rival:{rival_id}",
                "source": rival_id,
                "target": event_id,
                "predicate": "same name",
                "provenance": "candidate",
                "source_class": "candidate",
                "confidence": 0.0,
                "inferred": False,
                "rival": True,
                "label": f"score {candidate.get('graph_score', 0)}",
            }

        known_labels = {
            str(c["artist_node_id"]): str(c["label"]) for c in evidence.get("candidates") or ()
        }
        nodes = []
        for node_id, role in roles.items():
            node = {**self._node_payload(node_id), "role": role, "hops": 0 if role == "target" else 1}
            if node_id not in self.graph and node_id in known_labels:
                node["label"] = known_labels[node_id]
            nodes.append(node)
        return {
            "edge": _edge_payload(proposal),
            "evidence": evidence,
            "run_id": proposal.run_id,
            "event": self._node_payload(event_id),
            "artist": self._node_payload(artist_id),
            "nodes": nodes,
            "edges": list(edges.values()),
            "signals": signals,
            "rivals": rivals,
            "in_slice": event_id in self.graph,
            "rollback": (
                f"ALTER TABLE <inferred table> DELETE WHERE run_id = '{proposal.run_id}'"
            ),
        }

    def prediction(self, node_id: str, *, hide_known: bool, limit: int) -> dict[str, Any]:
        """Who the graph says plays this event, from structure alone, and along which paths.

        With `hide_known`, the event's own artist links are hidden first -- mask-and-recover,
        so an event whose answer is known can show whether the walk would have found it.
        """
        node = self.graph.node(node_id)
        if node.node_type is not NodeType.EVENT:
            raise ValueError(f"{node_id} is a {node.node_type.value}; predictions are for events")
        neighbourhood = self.walk_index
        known = neighbourhood.linked_artists(node_id)
        masked = known if hide_known else frozenset()
        predicted = neighbourhood.artists(node_id, masked, limit)

        roles: dict[str, str] = {node_id: "target"}
        edges: dict[str, dict[str, Any]] = {}
        rows = []
        for neighbour in predicted:
            roles[neighbour.artist_node_id] = (
                "true" if neighbour.artist_node_id in known else "predicted"
            )
            routes = []
            for route in neighbourhood.paths(node_id, masked, neighbour.artist_node_id):
                route_edges = []
                for left, right in zip(route, route[1:], strict=False):
                    edge = self._edge_between(left, right)
                    if edge is None:
                        continue
                    edges.setdefault(edge.edge_id, _edge_payload(edge))
                    route_edges.append(edge.edge_id)
                    for end in (left, right):
                        roles.setdefault(end, "path")
                routes.append({"nodes": route, "edges": route_edges})
            rows.append(
                {
                    "rank": neighbour.rank,
                    "artist_node_id": neighbour.artist_node_id,
                    "label": neighbour.label,
                    "score": neighbour.score,
                    "linked_events": neighbour.linked_events,
                    "is_true": neighbour.artist_node_id in known,
                    "paths": routes,
                }
            )

        truth = [
            {
                "artist_node_id": artist,
                "label": self._label(artist),
                "rank": neighbourhood.rank_of(node_id, masked, artist),
            }
            for artist in sorted(known)
        ]
        labels = {row["artist_node_id"]: f"#{row['rank']} {row['label']}" for row in rows}
        nodes = []
        for member, role in roles.items():
            payload = {**self._node_payload(member), "role": role, "hops": 0 if role == "target" else 1}
            if member in labels:
                payload["label"] = labels[member]
            nodes.append(payload)
        return {
            "event": self._node_payload(node_id),
            "hidden": len(masked),
            "known": len(known),
            "artist_pool": neighbourhood.artist_count,
            "predictions": rows,
            "truth": truth,
            "nodes": nodes,
            "edges": list(edges.values()),
        }

    def prediction_examples(self, sample: int) -> dict[str, Any]:
        """Hide the artists of a sample of events and see where the walk ranks them.

        The sample is deterministic -- the events with the lowest hash of their id -- so
        the same slice always gives the same list and the same rates.
        """
        neighbourhood = self.walk_index
        # an event at the slice's edge was loaded for one artist and has no venue of its
        # own in the slice, so its walk is starved; the market's own events have theirs
        events = sorted(
            (
                node.node_id
                for node in self.graph.nodes()
                if node.node_type is NodeType.EVENT
                and 1 <= len(neighbourhood.linked_artists(node.node_id)) <= MAX_BILL_FOR_EXAMPLE
                and any(True for _ in self.graph.incident(
                    node.node_id, predicates=frozenset({Predicate.HELD_AT}), direction=Direction.OUT
                ))
            ),
            key=lambda node_id: hashlib.blake2b(node_id.encode(), digest_size=8).digest(),
        )[:sample]
        rows = []
        hits = {1: 0, 10: 0}
        venue_hits = {1: 0, 10: 0}
        for event_id in events:
            known = neighbourhood.linked_artists(event_id)
            ranks = [r for a in known if (r := neighbourhood.rank_of(event_id, known, a))]
            best = min(ranks) if ranks else None
            venue_best = self._venue_rank(event_id, known)
            for k in hits:
                hits[k] += best is not None and best <= k
                venue_hits[k] += venue_best is not None and venue_best <= k
            rows.append(
                {
                    "event_node_id": event_id,
                    "label": self._label(event_id),
                    "rank": best,
                    "venue_rank": venue_best,
                }
            )
        rows.sort(key=lambda row: (row["rank"] is None, row["rank"] or 0))
        n = len(events) or 1
        return {
            "sample": len(events),
            "artist_pool": neighbourhood.artist_count,
            "recall_at_1": hits[1] / n,
            "recall_at_10": hits[10] / n,
            "venue_recall_at_1": venue_hits[1] / n,
            "venue_recall_at_10": venue_hits[10] / n,
            "rows": rows,
        }

    def _venue_rank(self, event_id: str, known: frozenset[str]) -> int | None:
        """The baseline: rank the venue's other acts by how often they were booked there."""
        adjacency = self.walk_index.adjacency
        event = adjacency.index[event_id]
        booked: Counter[int] = Counter()
        for venue in adjacency.venues_of.get(event, ()):
            for other in adjacency.neighbours[venue]:
                if other != event:
                    booked.update(adjacency.artists_of.get(other, ()))
        ranked = sorted(booked, key=lambda a: (-booked[a], -adjacency.performs[a]))
        wanted = {adjacency.index[a] for a in known if a in adjacency.index}
        return next((i for i, a in enumerate(ranked, 1) if a in wanted), None)

    def _edge_between(self, left: str, right: str) -> Edge | None:
        for edge, other in self.graph.incident(left):
            if other.node_id == right:
                return edge
        return None

    def _label(self, node_id: str) -> str:
        if node_id in self.graph:
            node = self.graph.node(node_id)
            return node.label or node.natural_key
        return node_id

    def _node_payload(self, node_id: str, via: Edge | None = None) -> dict[str, Any]:
        if node_id in self.graph:
            node = self.graph.node(node_id)
            return {
                "node_id": node_id,
                "node_type": node.node_type.value,
                "label": node.label or node.natural_key,
                "degree": self.graph.degree(node_id),
            }
        evidence = (via.evidence or {}) if via else {}
        label = evidence.get("artist_label") if via and node_id == via.src_node_id else None
        node_type, natural_key = parse_node_id(node_id)
        return {
            "node_id": node_id,
            "node_type": node_type.value,
            "label": label or natural_key,
            "degree": 0,
        }


def _edge_payload(edge: Edge) -> dict[str, Any]:
    return {
        "edge_id": edge.edge_id,
        "source": edge.src_node_id,
        "target": edge.dst_node_id,
        "predicate": edge.predicate.value,
        "provenance": edge.source,
        "source_class": edge.source_class.value,
        "confidence": round(edge.confidence, 3),
        "inferred": edge.is_inferred,
        "on_path": False,
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
            elif route.path == "/api/inferences":
                self._send_json(
                    self.explorer.inferences(
                        _first(query, "q", ""),
                        _first(query, "arm", ""),
                        limit=int(_first(query, "limit", "200")),
                        offset=int(_first(query, "offset", "0")),
                    )
                )
            elif route.path == "/api/predict":
                self._send_json(
                    self.explorer.prediction(
                        _first(query, "node", ""),
                        hide_known=_first(query, "hide_known", "1") == "1",
                        limit=int(_first(query, "limit", "10")),
                    )
                )
            elif route.path == "/api/predict/examples":
                self._send_json(
                    self.explorer.prediction_examples(int(_first(query, "sample", "200")))
                )
            elif route.path == "/api/inference":
                self._send_json(self.explorer.inference(_first(query, "edge", "")))
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
