"""Level-0 personal rescoring of recognition n-best lists.

This does not train weights. It boosts candidates that match a user's explicit
dictionary or previously confirmed preferred forms, and is always reversible.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RankedCandidate:
    text: str
    score: float
    reason: str = "base"


def rerank_candidates(
    candidates: list[tuple[str, float]],
    *,
    vocabulary: dict[str, str] | None = None,
    preferred_forms: dict[str, str] | None = None,
) -> list[RankedCandidate]:
    """Return candidates sorted by personal score, preserving every original.

    ``vocabulary`` maps a lowercased observed token to the user's preferred
    spelling. ``preferred_forms`` is an optional exact-string rewrite table.
    """
    vocab = {key.lower(): value for key, value in (vocabulary or {}).items()}
    preferred = preferred_forms or {}
    ranked: list[RankedCandidate] = []
    for text, score in candidates:
        reason = "base"
        adjusted = float(score)
        rewritten = preferred.get(text, text)
        if rewritten != text:
            adjusted += 0.15
            reason = "preferred_form"
            text = rewritten
        else:
            tokens = text.split()
            mapped = [vocab.get(token.lower(), token) for token in tokens]
            if mapped != tokens:
                text = " ".join(mapped)
                adjusted += 0.08
                reason = "vocabulary"
        ranked.append(RankedCandidate(text=text, score=adjusted, reason=reason))
    ranked.sort(key=lambda item: (-item.score, item.text))
    return ranked
