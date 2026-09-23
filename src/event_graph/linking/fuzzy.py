"""Matching a provider's spelling of a name to an artist row that is spelled differently.

Only ever applied to the few hundred artists in an event's graph neighbourhood. Against
all 1.2M rows the same rules are unusable -- 'Estiva' matches inside 'festival' -- but
inside the neighbourhood a near-match is already corroborated by structure.
"""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import IntEnum
from typing import Final

# a single-word spelling match shorter than this is a common word or an initialism -- "Luna
# (1)" is a *different* Luna, and the "(1)" is all that said so
SPELLING_MIN_LENGTH: Final = 5
TYPO_MIN_SIMILARITY: Final = 0.9
TYPO_MIN_LENGTH: Final = 5
# a name found inside another must be at least this specific to mean anything
CONTAINS_MIN_LENGTH: Final = 6
CONTAINS_MIN_TOKENS: Final = 2

_PARENTHESISED: Final = re.compile(r"[\(\[][^\)\]]*[\)\]]")
# "Dimitri (1)" is how a discography says *another* act of this name, so it never matches
_NUMBERED: Final = re.compile(r"\(\s*\d+\s*\)")
_NON_WORD: Final = re.compile(r"[^0-9a-z]+")
# "Red Band" is the band Red, and "DJ Sneak" is Sneak: descriptors, not part of the name
_DESCRIPTORS: Final = frozenset(
    {"dj", "live", "band", "official", "oficial", "music", "the", "and", "trio", "quartet"}
)


class MatchKind(IntEnum):
    """How strong a spelling match is; higher is stronger. Each kind is calibrated alone."""

    CONTAINS = 1
    TYPO = 2
    EQUAL = 3


@dataclass(frozen=True, slots=True)
class Match:
    kind: MatchKind
    similarity: float
    provider_form: str
    artist_form: str


def normalise(name: str) -> str:
    """Case, accents, punctuation, bracketed qualifiers and descriptor words removed."""
    text = _PARENTHESISED.sub(" ", name)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = text.replace("ø", "o").replace("æ", "ae").replace("ß", "ss").replace("&", " and ")
    tokens = [t for t in _NON_WORD.sub(" ", text).split() if t not in _DESCRIPTORS]
    return " ".join(tokens)


def _contains(outer: str, inner: str) -> bool:
    return f" {inner} " in f" {outer} "


def _specific(form: str) -> bool:
    return len(form) >= CONTAINS_MIN_LENGTH and (
        len(form.split()) >= CONTAINS_MIN_TOKENS or len(form) >= CONTAINS_MIN_LENGTH + 2
    )


def match(provider_name: str, artist_label: str) -> Match | None:
    """The strongest way the two spellings agree, or None when they do not."""
    if _NUMBERED.search(provider_name):
        return None
    provider, artist = normalise(provider_name), normalise(artist_label)
    if not provider or not artist:
        return None
    if len(provider.split()) == 1 and len(provider) < SPELLING_MIN_LENGTH:
        return None
    if provider == artist:
        return Match(MatchKind.EQUAL, 1.0, provider, artist)

    matcher = SequenceMatcher(None, provider, artist, autojunk=False)
    if (
        min(len(provider), len(artist)) >= TYPO_MIN_LENGTH
        and matcher.real_quick_ratio() >= TYPO_MIN_SIMILARITY
        and matcher.quick_ratio() >= TYPO_MIN_SIMILARITY
    ):
        similarity = matcher.ratio()
        if similarity >= TYPO_MIN_SIMILARITY:
            return Match(MatchKind.TYPO, similarity, provider, artist)

    # 'Concierto Ruth Lorenzo' names Ruth Lorenzo; 'Justin' alone does not name Justin Bieber
    if _specific(artist) and _contains(provider, artist):
        return Match(MatchKind.CONTAINS, len(artist) / len(provider), provider, artist)
    if len(provider.split()) >= CONTAINS_MIN_TOKENS and _specific(provider) and _contains(artist, provider):
        return Match(MatchKind.CONTAINS, len(provider) / len(artist), provider, artist)
    return None
