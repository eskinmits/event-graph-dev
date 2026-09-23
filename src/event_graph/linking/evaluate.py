"""How often each linking rule picks the artist the event is already linked to.

The labels are free: events that already carry an artist *and* a provider lineup name.
Two cautions are built in rather than left to the reader.

- **Circularity.** 137k `performs_at` edges have source `event-engine` -- they may have been
  made from these very names at import time, so agreeing with them is partly agreeing with
  the importer. Every number is also reported against independent links only (web,
  employee, automation), and calibration uses those when there are enough.
- **Baseline.** Ranking is reported as lift over picking the most-linked candidate (D7),
  never as raw accuracy.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Final

from event_graph.linking.linker import Arm, Decision

IMPORTER_SOURCE: Final = "event-engine"
# below this many independent labels a rule is calibrated on all labels instead, and below
# this many labels of any kind it proposes nothing: 18 labels put a precision within +-11pts
MIN_INDEPENDENT_LABELS: Final = 30
MIN_LABELS: Final = 30


@dataclass(slots=True)
class Tally:
    n: int = 0
    correct: int = 0

    def add(self, correct: bool) -> None:
        self.n += 1
        self.correct += correct

    @property
    def rate(self) -> float | None:
        return self.correct / self.n if self.n else None

    def describe(self) -> str:
        rate = self.rate
        return f"{rate:6.1%} of {self.n:>6,}" if rate is not None else f"{'--':>6} of {0:>6}"


@dataclass(slots=True)
class Evaluation:
    """Precision per rule, split by label provenance, plus the ranking lift."""

    # (arm, tribute fired) -> {"all" | "independent" -> tally}
    precision: dict[tuple[Arm, bool], dict[str, Tally]] = field(
        default_factory=lambda: defaultdict(lambda: {"all": Tally(), "independent": Tally()})
    )
    ranking_graph: dict[str, Tally] = field(
        default_factory=lambda: {"all": Tally(), "independent": Tally()}
    )
    ranking_popularity: dict[str, Tally] = field(
        default_factory=lambda: {"all": Tally(), "independent": Tally()}
    )

    def calibrated(self, arm: Arm, tribute: bool) -> tuple[float, int, str] | None:
        """The precision a proposal from this rule should carry, and what it rests on."""
        tallies = self.precision.get((arm, tribute))
        if not tallies:
            return None
        if tallies["all"].n < MIN_LABELS:
            return None
        for split in ("independent", "all"):
            tally = tallies[split]
            if tally.rate is not None and (split == "all" or tally.n >= MIN_INDEPENDENT_LABELS):
                return tally.rate, tally.n, split
        return None


def is_independent(decision: Decision) -> bool:
    """True when the event's links came from somewhere other than the importer."""
    return IMPORTER_SOURCE not in decision.mention.linked_artists.values()


def evaluate(decisions: list[Decision]) -> Evaluation:
    result = Evaluation()
    for decision in decisions:
        mention = decision.mention
        if mention.is_artistless or not decision.ranked or decision.arm is None:
            continue
        splits = ("all", "independent") if is_independent(decision) else ("all",)

        chosen = decision.chosen
        if chosen is not None:
            correct = chosen.candidate.artist_node_id in mention.linked_artists
            for split in splits:
                result.precision[(decision.arm, decision.tribute is not None)][split].add(correct)

        truth = mention.truth()
        if mention.is_ambiguous and truth is not None:
            graph_pick = decision.ranked[0].candidate
            popular_pick = decision.popularity_choice()
            for split in splits:
                result.ranking_graph[split].add(graph_pick == truth)
                result.ranking_popularity[split].add(popular_pick == truth)
    return result


def report(evaluation: Evaluation) -> str:
    lines = ["precision by rule (chosen artist is among the event's existing links)"]
    lines.append(f"  {'rule':<18} {'tribute':<8} {'all labels':>16}   {'independent labels':>18}")
    for (arm, tribute), tallies in sorted(evaluation.precision.items()):
        lines.append(
            f"  {arm.value:<18} {'yes' if tribute else 'no':<8} "
            f"{tallies['all'].describe():>16}   {tallies['independent'].describe():>18}"
        )
    lines.append("\nranking among same-name rows, top-1 against the one linked row")
    for split in ("all", "independent"):
        graph = evaluation.ranking_graph[split]
        popular = evaluation.ranking_popularity[split]
        lift = (
            f"{graph.rate - popular.rate:+.1%} points"
            if graph.rate is not None and popular.rate is not None
            else "--"
        )
        lines.append(
            f"  {split:<12} graph {graph.describe()}   most-linked {popular.describe()}   lift {lift}"
        )
    return "\n".join(lines)
