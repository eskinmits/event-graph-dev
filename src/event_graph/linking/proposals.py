"""Turning decisions on artist-less upcoming events into explained, calibrated proposals.

A proposal's confidence is the measured precision of the rule that produced it, in this
market, on events whose answer is already known -- not a number picked by hand. Anything
under the precision floor is kept out of the worklist rather than shown with a warning.
"""

from dataclasses import dataclass
from typing import Any, Final

from event_graph.graph.edges import Edge
from event_graph.graph.ontology import Predicate
from event_graph.linking.evaluate import Evaluation
from event_graph.linking.guards import TRIBUTE_CONFIDENCE_CAP
from event_graph.linking.linker import Decision

DEFAULT_PRECISION_FLOOR: Final = 0.8
RULE: Final = "event_engine_lineup_name"
MAX_CANDIDATES_IN_EVIDENCE: Final = 6


@dataclass(frozen=True, slots=True)
class Proposal:
    edge: Edge
    decision: Decision

    @property
    def artist_label(self) -> str:
        chosen = self.decision.chosen
        return chosen.candidate.label if chosen else ""

    def explain(self) -> str:
        evidence = self.edge.evidence or {}
        signals = evidence.get("graph_signals") or {}
        parts = [f"provider {evidence.get('provider_id')} lists {evidence.get('provider_name')!r}"]
        if evidence.get("same_name_rows", 1) > 1:
            parts.append(f"{evidence['same_name_rows']} artist rows share the name")
        if signals:
            parts.append(", ".join(f"{k} {v}" for k, v in signals.items()))
        parts.append(f"rule precision {evidence.get('rule_precision', 0):.0%}")
        return " · ".join(parts)


def propose(
    decisions: list[Decision],
    evaluation: Evaluation,
    *,
    run_id: str,
    eval_command: str,
    precision_floor: float = DEFAULT_PRECISION_FLOOR,
) -> tuple[list[Proposal], dict[str, int]]:
    """Proposals for artist-less upcoming events, and a count of why the rest were dropped."""
    proposals: dict[tuple[str, str], Proposal] = {}
    dropped: dict[str, int] = {}

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    for decision in decisions:
        mention = decision.mention
        if not (mention.is_artistless and mention.is_upcoming):
            continue
        chosen = decision.chosen
        if chosen is None or decision.arm is None:
            drop(decision.blocked or "no candidate")
            continue
        rule = f"{decision.arm.value}{' + tribute' if decision.tribute else ''}"
        calibration = evaluation.calibrated(decision.arm, decision.tribute is not None)
        if calibration is None:
            drop(f"{rule}: too few labelled cases to calibrate on")
            continue
        precision, labels, split = calibration
        confidence = min(precision, TRIBUTE_CONFIDENCE_CAP) if decision.tribute else precision
        if confidence < precision_floor:
            drop(f"{rule}: below floor")
            continue

        runner_up = decision.runner_up
        evidence: dict[str, Any] = {
            "rule": RULE,
            "arm": decision.arm.value,
            "external_event_id": mention.external_event_id,
            "provider_id": mention.provider_id,
            "provider_name": mention.name,
            "event_title": mention.title,
            "artist_label": chosen.candidate.label,
            "same_name_rows": len(decision.ranked),
            "candidates": [
                {
                    "artist_node_id": s.candidate.artist_node_id,
                    "label": s.candidate.label,
                    "graph_score": s.features.score,
                    "graph_signals": s.features.reasons(),
                    "linked_events": s.popularity,
                }
                for s in decision.ranked[:MAX_CANDIDATES_IN_EVIDENCE]
            ],
            "cobilled": sorted(decision.cobilled),
            "neighbourhood_match": (
                {
                    "kind": decision.matches[0].match.kind.name.lower(),
                    "similarity": round(decision.matches[0].match.similarity, 3),
                    "provider_form": decision.matches[0].match.provider_form,
                    "artist_form": decision.matches[0].match.artist_form,
                    "walk_rank": decision.matches[0].neighbour.rank,
                    "walk_score": round(decision.matches[0].neighbour.score, 6),
                    "neighbourhood_size": decision.matches[0].neighbourhood_size,
                    "other_matches": len(decision.matches) - 1,
                }
                if decision.matches
                else None
            ),
            "graph_signals": chosen.features.reasons(),
            "graph_score": chosen.features.score,
            "linked_events": chosen.popularity,
            "runner_up": (
                {
                    "artist_node_id": runner_up.candidate.artist_node_id,
                    "graph_score": runner_up.features.score,
                    "linked_events": runner_up.popularity,
                }
                if runner_up
                else None
            ),
            "tribute_token": decision.tribute,
            "rule_precision": round(precision, 4),
            "calibrated_on": {"labels": labels, "split": split, "command": eval_command},
        }
        edge = Edge.inferred(
            chosen.candidate.artist_node_id,
            Predicate.PERFORMS_AT,
            mention.event_node_id,
            confidence=round(confidence, 4),
            run_id=run_id,
            evidence=evidence,
        )
        key = (edge.src_node_id, edge.dst_node_id)
        if key not in proposals or proposals[key].edge.confidence < edge.confidence:
            proposals[key] = Proposal(edge, decision)

    ranked = sorted(proposals.values(), key=lambda p: (-p.edge.confidence, p.edge.dst_node_id))
    return ranked, dropped
