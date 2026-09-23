"""Reasons not to trust a name match, applied before anything is ranked.

Each guard returns the reason it fired rather than a boolean, so a downgraded or dropped
proposal carries its explanation into `evidence` -- a blocked match has to be auditable.
The token lists are the ones on the Ontology & Edge Model page, per market.
"""

import re
from typing import Final

from event_graph.graph.filters import DEFAULT_JUNK_HUB_LABELS

# never blocked outright, since "X + support" bills exist; downgraded below any floor instead
TRIBUTE_CONFIDENCE_CAP: Final = 0.2

TRIBUTE_TOKENS: Final[tuple[str, ...]] = (
    # en
    "tribute", "cover", "covers", "plays", "the music of", "a night of", "celebrating",
    "candlelight", "by candlelight", "experience", "orchestra plays", "symphonic", "sings",
    "the world of",
    # es
    "tributo", "homenaje", "versiones", "versión", "la música de", "las canciones de",
    "noche de", "a la luz de las velas", "sinfónico", "banda tributo", "cover band", "canta a",
    # nl
    "hommage", "de muziek van", "eerbetoon", "coverband", "bij kaarslicht", "zingt",
    # de
    "die musik von", "bei kerzenschein", "singt",
)

_TRIBUTE_RE: Final = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in sorted(TRIBUTE_TOKENS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# verbs put the performer first -- "Niels Geusebroek sings Coldplay" -- while noun tokens put
# the covered act first -- "Queen Tribute", "The Coldplay Experience"
PERFORMER_FIRST_TOKENS: Final[frozenset[str]] = frozenset(
    {"sings", "plays", "orchestra plays", "zingt", "singt", "canta a"}
)
# "Bee Gees Forever (by MainCourse)" names a cover act. Only inside a provider's name,
# though: in a title "by" names the host or label -- "EXHALE by Amelie Lens"
_COVERED_FIRST_RE: Final = re.compile(r"\b(by|door)\b", re.IGNORECASE)

# the placeholders junk_nodes.py flags by vocabulary; a provider writing "TBA" is not an act
PLACEHOLDER_NAMES: Final[frozenset[str]] = DEFAULT_JUNK_HUB_LABELS | {
    "tba", "tbc", "tbd", "to be announced", "various artists", "various", "va",
    "special guest", "special guests", "guests", "support", "live", "dj", "djs", "karaoke",
}

# a name carried by this many live artist rows is a common word ("luna" has 19), so a match
# on it says nothing until the graph corroborates it
COMMON_WORD_MIN_ROWS: Final = 5


def tribute_token(name: str, title: str) -> str | None:
    """The tribute or cover token that makes this name the act being covered, if any.

    A token inside the name itself always counts. In the title, a verb token only counts
    against names after it: in "Niels Geusebroek sings Coldplay" Niels performs and Coldplay
    is covered. A noun token such as "tribute" counts wherever the name sits.
    """
    in_name = _TRIBUTE_RE.search(name) or _COVERED_FIRST_RE.search(name)
    if in_name:
        return in_name.group(1).casefold()
    at = title.casefold().find(name.casefold().strip())
    in_title = _TRIBUTE_RE.search(title)
    if not in_title:
        return None
    token = in_title.group(1).casefold()
    if token in PERFORMER_FIRST_TOKENS and 0 <= at < in_title.start():
        return None
    return token


def is_placeholder(name: str) -> bool:
    return name.casefold().strip() in PLACEHOLDER_NAMES


def is_common_word(candidate_count: int) -> bool:
    return candidate_count >= COMMON_WORD_MIN_ROWS

