"""Evaluation metrics for formula and page recognition.

The functions here keep strict transcription metrics separate from optional
rendered-structure metrics.  Missing benchmark dependencies return an explicit
``None`` rather than fabricating a score.
"""

from __future__ import annotations

import math
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence


@dataclass(frozen=True)
class ExpressionMetrics:
    exact_match: float
    token_error_rate: float
    render_success: float
    truncated_rate: float
    count: int
    normalized_exact_match: float | None = None
    critical_structure_error_rate: float | None = None
    cdm: float | None = None


def _tokens(text: str) -> list[str]:
    from app.ml.math.tokenizer import LatexTokenizer

    return LatexTokenizer.default().split(text)


_NORMALIZATION_ALIASES = {
    r"\dfrac": r"\frac",
    r"\tfrac": r"\frac",
    r"\le": r"\leq",
    r"\ge": r"\geq",
    r"\ne": r"\neq",
}


def normalize_latex(text: str) -> str:
    """Apply only documented syntactic normalization, never algebraic equivalence."""
    normalized = text.strip()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.replace(r"\left", "").replace(r"\right", "")
    for source, target in _NORMALIZATION_ALIASES.items():
        normalized = re.sub(
            rf"(?<![A-Za-z]){re.escape(source)}(?![A-Za-z])",
            lambda _match, value=target: value,
            normalized,
        )
    return normalized


def normalized_exact_match(reference: str, hypothesis: str) -> bool:
    return normalize_latex(reference) == normalize_latex(hypothesis)


def _edit_distance(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    dp = list(range(len(hypothesis) + 1))
    for i, ref in enumerate(reference, start=1):
        previous = dp[0]
        dp[0] = i
        for j, hyp in enumerate(hypothesis, start=1):
            current = dp[j]
            dp[j] = previous if ref == hyp else 1 + min(previous, dp[j], dp[j - 1])
            previous = current
    return dp[-1]


def character_error_rate(reference: str, hypothesis: str) -> float:
    ref = list(reference)
    hyp = list(hypothesis)
    return _edit_distance(ref, hyp) / max(1, len(ref))


def word_error_rate(reference: str, hypothesis: str) -> float:
    return _edit_distance(reference.split(), hypothesis.split()) / max(1, len(reference.split()))


_CRITICAL_TOKENS = (r"\frac", r"\sqrt", r"\sum", r"\int", "-", "=", "^", "_", "{", "}", r"\leq", r"\geq", r"\neq")


def critical_structure_errors(reference: str, hypothesis: str) -> dict[str, int]:
    """Count high-impact operator/structure count mismatches."""
    ref = Counter(_tokens(normalize_latex(reference)))
    hyp = Counter(_tokens(normalize_latex(hypothesis)))
    return {token: max(0, ref[token] - hyp[token]) for token in _CRITICAL_TOKENS if ref[token] != hyp[token]}


def critical_structure_error_rate(pairs: Iterable[tuple[str, str]]) -> float:
    values = list(pairs)
    if not values:
        return 0.0
    return sum(bool(critical_structure_errors(ref, hyp)) for ref, hyp in values) / len(values)


def _latex_syntax_is_renderable(text: str) -> bool:
    """Perform a conservative renderability check without hiding failures.

    The benchmark package deliberately has no TeX engine dependency, so this
    is not a visual-equivalence claim.  It does reject unsupported commands
    and unbalanced TeX grouping, which prevents malformed output from being
    counted as successfully rendered merely because decoding was not
    truncated.
    """
    text = normalize_latex(text)
    if not text.strip():
        return False

    from app.ml.math.tokenizer import LatexTokenizer, UnsupportedLatexError

    try:
        LatexTokenizer.default().encode(text, add_bos_eos=False)
    except UnsupportedLatexError:
        return False

    stack: list[str] = []
    escaped = False
    pairs = {"}": "{", "]": "[", ")": "("}
    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in "{[(":
            stack.append(char)
        elif char in pairs:
            if not stack or stack.pop() != pairs[char]:
                return False
    return not stack and not escaped


def token_error_rate(reference: str, hypothesis: str) -> float:
    ref = _tokens(normalize_latex(reference))
    hyp = _tokens(normalize_latex(hypothesis))
    if not ref:
        return 0.0 if not hyp else 1.0
    # Levenshtein on tokens.
    dp = list(range(len(hyp) + 1))
    for i, ref_tok in enumerate(ref, start=1):
        prev = dp[0]
        dp[0] = i
        for j, hyp_tok in enumerate(hyp, start=1):
            cur = dp[j]
            if ref_tok == hyp_tok:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = cur
    return dp[-1] / len(ref)


def summarize(
    pairs: list[tuple[str, str]],
    *,
    truncated: list[bool] | None = None,
) -> ExpressionMetrics:
    if not pairs:
        return ExpressionMetrics(0.0, 0.0, 0.0, 0.0, 0)
    exact = 0
    ter = 0.0
    if truncated is not None and len(truncated) != len(pairs):
        raise ValueError("truncated must have the same length as pairs")
    truncated_flags = truncated if truncated is not None else [False] * len(pairs)
    renderable = 0
    for (ref, hyp), was_truncated in zip(pairs, truncated_flags):
        exact += int(normalized_exact_match(ref, hyp))
        ter += token_error_rate(ref, hyp)
        renderable += int(not was_truncated and _latex_syntax_is_renderable(hyp))
    n = len(pairs)
    trunc_rate = sum(1 for flag in truncated_flags if flag) / n
    return ExpressionMetrics(
        exact_match=exact / n,
        token_error_rate=ter / n,
        render_success=renderable / n,
        truncated_rate=trunc_rate,
        count=n,
        normalized_exact_match=exact / n,
        critical_structure_error_rate=critical_structure_error_rate(pairs),
    )


@dataclass(frozen=True)
class PageMetrics:
    """Page-level recall/reading-order summary."""

    layout_recall: float
    order_accuracy: float
    end_to_end_accuracy: float
    page_count: int


def summarize_page_layout(
    pages: Sequence[dict[str, object]],
) -> PageMetrics:
    """Summarize page records with ``expected`` and ``predicted`` region ids.

    A page is an independent unit, preventing pages with many crops from
    dominating the product metric.
    """
    if not pages:
        return PageMetrics(0.0, 0.0, 0.0, 0)
    recalls: list[float] = []
    orders: list[float] = []
    complete: list[float] = []
    for page in pages:
        expected = [str(value) for value in (page.get("expected") or [])]
        predicted = [str(value) for value in (page.get("predicted") or [])]
        expected_set = set(expected)
        recalls.append(len(expected_set & set(predicted)) / max(1, len(expected_set)))
        common = min(len(expected), len(predicted))
        orders.append(sum(expected[i] == predicted[i] for i in range(common)) / max(1, len(expected)))
        complete.append(float(bool(expected) and expected == predicted and bool(page.get("correct", True))))
    return PageMetrics(sum(recalls) / len(recalls), sum(orders) / len(orders), sum(complete) / len(complete), len(pages))


def paired_bootstrap_ci(
    values: Sequence[float],
    *,
    groups: Sequence[str] | None = None,
    statistic: Callable[[Sequence[float]], float] = lambda sample: sum(sample) / max(1, len(sample)),
    resamples: int = 2000,
    seed: int = 1337,
) -> tuple[float, float, float]:
    """Return estimate and a 95% paired bootstrap interval, grouped by page."""
    if len(values) == 0:
        return (0.0, 0.0, 0.0)
    if groups is None:
        groups = [str(index) for index in range(len(values))]
    if len(groups) != len(values):
        raise ValueError("groups must have the same length as values")
    grouped: dict[str, list[float]] = defaultdict(list)
    for group, value in zip(groups, values):
        grouped[str(group)].append(float(value))
    keys = sorted(grouped)
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(max(1, resamples)):
        sample: list[float] = []
        for _ in keys:
            sample.extend(grouped[rng.choice(keys)])
        estimates.append(float(statistic(sample)))
    estimates.sort()
    low = estimates[int(0.025 * (len(estimates) - 1))]
    high = estimates[int(0.975 * (len(estimates) - 1))]
    return float(statistic(values)), low, high


def paired_bootstrap_delta_ci(
    before: Sequence[float],
    after: Sequence[float],
    *,
    groups: Sequence[str] | None = None,
    statistic: Callable[[Sequence[float]], float] = lambda sample: sum(sample) / max(1, len(sample)),
    resamples: int = 2000,
    seed: int = 1337,
) -> tuple[float, float, float]:
    """Return the paired ``after - before`` estimate and a 95% page bootstrap CI.

    ``before`` and ``after`` must describe the same samples in the same order.
    Resampling whole page groups preserves the dependence between the two
    measurements and prevents pages with many crops from dominating the
    uncertainty estimate.  A negative CER/WER delta therefore means that the
    after model improved.
    """
    if len(before) != len(after):
        raise ValueError("before and after must have the same length")
    if not before:
        return (0.0, 0.0, 0.0)
    if groups is None:
        groups = [str(index) for index in range(len(before))]
    if len(groups) != len(before):
        raise ValueError("groups must have the same length as before and after")

    grouped: dict[str, list[float]] = defaultdict(list)
    for group, old, new in zip(groups, before, after):
        grouped[str(group)].append(float(new) - float(old))
    keys = sorted(grouped)
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(max(1, resamples)):
        sample: list[float] = []
        for _ in keys:
            sample.extend(grouped[rng.choice(keys)])
        estimates.append(float(statistic(sample)))
    estimates.sort()
    low = estimates[int(0.025 * (len(estimates) - 1))]
    high = estimates[int(0.975 * (len(estimates) - 1))]
    point = statistic([float(new) - float(old) for old, new in zip(before, after)])
    return float(point), low, high


def render_formula(text: str) -> object | None:
    """Render through matplotlib when available; return None otherwise."""
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return None
    if not _latex_syntax_is_renderable(text):
        return None
    try:
        figure = plt.figure(figsize=(1, 1))
        figure.text(0.05, 0.5, f"${text}$", fontsize=14)
        figure.canvas.draw()
        return figure
    except Exception:
        try:
            plt.close("all")
        except Exception:
            pass
        return None
