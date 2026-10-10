"""Suggestions for misspelled keys, types and values."""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable


def _edit_distance(a: str, b: str) -> int:
    """Optimal string alignment distance: insertions, deletions, substitutions and adjacent swaps."""
    rows = [list(range(len(b) + 1))]
    for i in range(1, len(a) + 1):
        row = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            row[j] = min(rows[i - 1][j] + 1, row[j - 1] + 1, rows[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                row[j] = min(row[j], rows[i - 2][j - 2] + 1)
        rows.append(row)
    return rows[-1][-1]


def closest(word: str, candidates: Iterable[str], cutoff: float = 0.6) -> str | None:
    pool = [c for c in candidates if isinstance(c, str)]
    by_lower = {c.lower(): c for c in pool}
    if word.lower() in by_lower and by_lower[word.lower()] != word:
        return by_lower[word.lower()]
    matches = difflib.get_close_matches(word, pool, n=5, cutoff=cutoff)
    if not matches:
        return None

    def prefix_related(m: str) -> bool:  # justifyContent -> justify, textt -> text
        a, b = word.lower(), m.lower()
        return min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a))

    # Prefer prefix relatives, then edit distance; on ties anagrams (swapped letters are the commonest typo).
    return min(matches, key=lambda m: (not prefix_related(m), _edit_distance(word.lower(), m.lower()),
                                       sorted(m) != sorted(word), -difflib.SequenceMatcher(None, word, m).ratio()))


def listing(candidates: Iterable[str], limit: int = 8) -> str:
    items = sorted({str(c) for c in candidates})
    shown = ", ".join(items[:limit])
    return shown + (f", ... ({len(items)} total)" if len(items) > limit else "")


def did_you_mean(word: str, candidates: Iterable[str], cutoff: float = 0.6, limit: int = 8) -> str:
    pool = list(candidates)
    match = closest(word, pool, cutoff)
    if match:
        return f"did you mean '{match}'?"
    return f"valid: {listing(pool, limit)}" if pool else "remove it"


def camel_case(name: str) -> str:
    """`background-color` -> `backgroundColor`, `-webkit-line-clamp` -> `WebkitLineClamp`."""
    head, *rest = name.lstrip("-").split("-")
    camel = head + "".join(part[:1].upper() + part[1:] for part in rest)
    return camel[:1].upper() + camel[1:] if name.startswith("-") else camel


def is_kebab(name: str) -> bool:
    return bool(re.fullmatch(r"-?[a-z]+(-[a-z0-9]+)+", name))
