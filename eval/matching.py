"""Provisional automated matching of (component, mechanism) against an answer key.

Milestone 2 s5: a diagnosis is correct only if BOTH component and mechanism match the
answer key, allowing predefined equivalent wordings (AnswerKey.accepted_*). This module
is a lexical approximation. Every verdict it produces is PROVISIONAL and is written to
the review worksheets for two human reviewers to confirm or overturn.

Rules (judgment calls, documented in the report):
- Text is lower-cased, punctuation becomes whitespace, "-"/"_"/"/"/"." split tokens,
  and a trailing plural "s" is dropped from tokens longer than 3 characters.
- Component: an accepted wording matches when its token sequence appears contiguously
  in the reported component (so "inventory db" matches "inventory-db connection pool").
- Mechanism: an accepted wording matches when its token sequence appears contiguously
  in the reported mechanism, OR every content token (stopwords removed) of the
  accepted wording appears somewhere in the reported mechanism.
- An empty accepted list (no component/mechanism in the key) never matches.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from blackbox.schemas import AnswerKey, FinalReport, RankedCause

STOPWORDS = frozenset("""
a an the of to in on at for by with from and or is are was were be been being due
because caused cause causing its it this that which as into than then via after before
""".split())


def _stem(tok: str) -> str:
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def tokens(text: Optional[str]) -> list[str]:
    if not text:
        return []
    t = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return [_stem(x) for x in t.split()]


def _contains_seq(hay: list[str], needle: list[str]) -> bool:
    if not needle or len(needle) > len(hay):
        return False
    n = len(needle)
    return any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def accepted_components(key: AnswerKey) -> list[str]:
    vals = ([key.component] if key.component else []) + list(key.accepted_components)
    return [v for v in vals if v and v.strip()]


def accepted_mechanisms(key: AnswerKey) -> list[str]:
    vals = ([key.mechanism] if key.mechanism else []) + list(key.accepted_mechanisms)
    return [v for v in vals if v and v.strip()]


def component_matches(reported: str, accepted: Iterable[str]) -> bool:
    hay = tokens(reported)
    return any(_contains_seq(hay, tokens(a)) for a in accepted)


def mechanism_matches(reported: str, accepted: Iterable[str]) -> bool:
    hay = tokens(reported)
    hay_set = set(hay)
    for a in accepted:
        at = tokens(a)
        if _contains_seq(hay, at):
            return True
        content = [t for t in at if t not in STOPWORDS]
        if content and all(t in hay_set for t in content):
            return True
    return False


def cause_matches(cause: RankedCause, key: AnswerKey) -> bool:
    return (component_matches(cause.component, accepted_components(key))
            and mechanism_matches(cause.mechanism, accepted_mechanisms(key)))


def ranked(report: FinalReport) -> list[RankedCause]:
    """Causes ordered by rank, at most three (the report contract allows three)."""
    return sorted(report.ranked_causes, key=lambda c: c.rank)[:3]


def match_rank(report: Optional[FinalReport], key: AnswerKey) -> Optional[int]:
    """1-based position of the first matching cause among the top three, else None.

    Only reports with status "diagnosis" can match: an abstention that lists tentative
    causes is still an abstention (counts as a miss), per Milestone 2 s5.
    """
    if report is None or report.status != "diagnosis":
        return None
    for i, c in enumerate(ranked(report), start=1):
        if cause_matches(c, key):
            return i
    return None
