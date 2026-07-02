"""Reproducibility smoke test for the base-model training procedure (task 6.6).

Requirement 11.3 requires that re-executing the documented training procedure on
the same dataset and configuration produces a Base_Model whose CER and WER on a
*fixed evaluation set* differ from a previously produced Base_Model by no more
than a configured tolerance.

This smoke test exercises that contract end-to-end on a tiny synthetic dataset so
it runs in seconds and never downloads IAM:

1. Train the base model twice with the same fixed seed / pinned config / data.
2. Greedily CTC-decode each trained model's predictions on a *fixed* tiny eval
   set (the same references and images for both runs).
3. Measure CER and WER of each run on that eval set.
4. Assert the per-run CER/WER differ by no more than ``REPRO_TOLERANCE``.

Because determinism (fixed seeds + deterministic data ordering, see
``app.config.settings.configure_determinism`` and ``train_base_model``) makes the
two runs produce identical weights, the expected difference is exactly 0; the
small tolerance leaves headroom for any platform-level nondeterminism while still
catching a genuine reproducibility regression.

CER/WER note
------------
The shared Evaluation_Service metrics (task 15.1) are not a dependency of this
task, so a minimal, self-contained CER/WER helper is implemented locally here:
character-/word-level Levenshtein edit distance over the *reference* length,
matching the definitions in Requirements 7.4 and 7.5.
"""

from __future__ import annotations

import torch

from app.config.settings import Settings
from app.ml.htr_config import ALPHABET, HTRModelConfig, TrainingConfig
from app.ml.htr_model import CRNN
from app.ml.train_base import LabeledSample, train_base_model

# Configured reproducibility tolerance (Req 11.3). Determinism should make the
# two runs identical (diff == 0); this leaves a tiny margin for platform-level
# nondeterminism while still failing on a real reproducibility regression.
REPRO_TOLERANCE: float = 1e-6


# ---------------------------------------------------------------------------
# Minimal, self-contained CER/WER helper (Req 7.4, 7.5 definitions)
# ---------------------------------------------------------------------------


def _levenshtein(reference: list, hypothesis: list) -> int:
    """Levenshtein edit distance (substitutions + insertions + deletions).

    Works over any sequence of comparable tokens (characters or words).
    """
    m, n = len(reference), len(hypothesis)
    if m == 0:
        return n
    if n == 0:
        return m
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        curr = [i] + [0] * n
        for j in range(1, n + 1):
            cost = 0 if reference[i - 1] == hypothesis[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,  # deletion
                curr[j - 1] + 1,  # insertion
                prev[j - 1] + cost,  # substitution
            )
        prev = curr
    return prev[n]


def _cer(reference: str, hypothesis: str) -> float:
    """Character Error Rate: char-level edit distance / reference char count."""
    ref_chars = list(reference)
    # An empty reference cannot yield a meaningful ratio; treat as unavailable.
    assert ref_chars, "CER is undefined for an empty reference"
    return _levenshtein(ref_chars, list(hypothesis)) / len(ref_chars)


def _wer(reference: str, hypothesis: str) -> float:
    """Word Error Rate: word-level edit distance / reference word count."""
    ref_words = reference.split()
    assert ref_words, "WER is undefined for a reference with no words"
    return _levenshtein(ref_words, hypothesis.split()) / len(ref_words)


def _corpus_cer_wer(
    references: list[str], hypotheses: list[str]
) -> tuple[float, float]:
    """Aggregate CER/WER over a fixed eval set (mean over per-sample ratios)."""
    assert len(references) == len(hypotheses)
    cers = [_cer(r, h) for r, h in zip(references, hypotheses)]
    wers = [_wer(r, h) for r, h in zip(references, hypotheses)]
    return sum(cers) / len(cers), sum(wers) / len(wers)


# ---------------------------------------------------------------------------
# Greedy CTC decoding (turn model log-probs into recognized text)
# ---------------------------------------------------------------------------

# CTC blank occupies index 0; characters map to indices 1..len(ALPHABET).
_INDEX_TO_CHAR: dict[int, str] = {i + 1: ch for i, ch in enumerate(ALPHABET)}


def _greedy_ctc_decode(log_probs: torch.Tensor) -> list[str]:
    """Greedily decode CTC log-probs of shape ``(T, N, C)`` into strings.

    For each sequence: take the per-timestep argmax, collapse consecutive
    duplicates, then drop the blank (index 0), mapping the remaining indices
    back to alphabet characters.
    """
    best = log_probs.argmax(dim=2)  # (T, N)
    t, n = best.shape
    decoded: list[str] = []
    for seq_idx in range(n):
        chars: list[str] = []
        prev = -1
        for step in range(t):
            idx = int(best[step, seq_idx])
            if idx != prev and idx != 0:
                ch = _INDEX_TO_CHAR.get(idx)
                if ch is not None:
                    chars.append(ch)
            prev = idx
        decoded.append("".join(chars))
    return decoded


def _predict(model: CRNN, samples: list[LabeledSample]) -> list[str]:
    """Run each eval image through the (frozen) model and CTC-decode it."""
    predictions: list[str] = []
    model.eval()
    with torch.no_grad():
        for sample in samples:
            log_probs = model(sample.image.unsqueeze(0))  # (T, 1, C)
            predictions.extend(_greedy_ctc_decode(log_probs))
    return predictions


# ---------------------------------------------------------------------------
# Fixtures: tiny config, fixed training data, and a FIXED eval set
# ---------------------------------------------------------------------------


def _tiny_config() -> HTRModelConfig:
    """A small but architecture-faithful config so the run is fast."""
    return HTRModelConfig(
        image_height=16,
        cnn_channels=(8, 16, 32, 32),
        lstm_hidden_size=16,
        lstm_num_layers=1,
        training=TrainingConfig(batch_size=2, max_epochs=80),
    )


def _synthetic_dataset(config: HTRModelConfig, n: int = 6) -> list[LabeledSample]:
    """Deterministic tiny training set of line images + labels.

    Seeded explicitly so the *data* is identical across runs; combined with the
    trainer's own seeded init/ordering, the whole procedure is reproducible.
    """
    gen = torch.Generator().manual_seed(0)
    words = ["cat", "dog", "Hi", "note", "the", "a"]
    samples: list[LabeledSample] = []
    for i in range(n):
        width = 48 + (i % 3) * 16
        image = torch.rand(
            config.input_channels, config.image_height, width, generator=gen
        )
        samples.append(LabeledSample(image=image, text=words[i % len(words)]))
    return samples


def _fixed_eval_set(config: HTRModelConfig) -> list[LabeledSample]:
    """A FIXED tiny evaluation set (same images + references for every run).

    A separate generator seed keeps the eval images distinct from the training
    images while remaining perfectly reproducible across runs.
    """
    gen = torch.Generator().manual_seed(12345)
    references = ["the", "cat", "note"]
    samples: list[LabeledSample] = []
    for i, text in enumerate(references):
        width = 48 + (i % 3) * 16
        image = torch.rand(
            config.input_channels, config.image_height, width, generator=gen
        )
        samples.append(LabeledSample(image=image, text=text))
    return samples


def _test_settings(tmp_path) -> Settings:
    return Settings(object_store_path=str(tmp_path / "objectstore"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cer_wer_helper_matches_definitions():
    """Sanity-check the local metric helper against known examples (Req 7.4/7.5)."""
    # Identical strings => zero error.
    assert _cer("note", "note") == 0.0
    assert _wer("the cat", "the cat") == 0.0
    # One substituted character out of four reference chars => 0.25 CER.
    assert _cer("note", "nole") == 0.25
    # One wrong word out of two reference words => 0.5 WER.
    assert _wer("the cat", "the dog") == 0.5
    # Metrics are non-negative ratios.
    assert _cer("abc", "") >= 0.0
    assert _wer("a b c", "") >= 0.0


def test_base_training_is_reproducible_on_fixed_eval_set(tmp_path):
    """Re-running training yields CER/WER within tolerance on a fixed eval set.

    Validates Requirement 11.3: the documented procedure, re-executed with the
    same dataset + configuration, reproduces base-model accuracy on a fixed
    evaluation set to within the configured tolerance.
    """
    config = _tiny_config()
    dataset = _synthetic_dataset(config)
    eval_set = _fixed_eval_set(config)
    references = [s.text for s in eval_set]

    # First run.
    settings_a = _test_settings(tmp_path / "run_a")
    result_a = train_base_model(
        dataset, config=config, max_epochs=3, settings=settings_a
    )
    preds_a = _predict(result_a.model, eval_set)
    cer_a, wer_a = _corpus_cer_wer(references, preds_a)

    # Second run with the same seed / config / data.
    settings_b = _test_settings(tmp_path / "run_b")
    result_b = train_base_model(
        dataset, config=config, max_epochs=3, settings=settings_b
    )
    preds_b = _predict(result_b.model, eval_set)
    cer_b, wer_b = _corpus_cer_wer(references, preds_b)

    # The fixed eval references and images are identical across runs.
    assert [s.text for s in eval_set] == references

    # Accuracy on the fixed eval set is reproducible within the configured
    # tolerance (Req 11.3). Determinism makes this difference exactly 0.
    assert abs(cer_a - cer_b) <= REPRO_TOLERANCE, (
        f"CER not reproducible: run_a={cer_a}, run_b={cer_b}, "
        f"diff={abs(cer_a - cer_b)} > tol={REPRO_TOLERANCE}"
    )
    assert abs(wer_a - wer_b) <= REPRO_TOLERANCE, (
        f"WER not reproducible: run_a={wer_a}, run_b={wer_b}, "
        f"diff={abs(wer_a - wer_b)} > tol={REPRO_TOLERANCE}"
    )

    # Sanity: metrics are valid non-negative ratios.
    assert cer_a >= 0.0 and wer_a >= 0.0


def test_identical_decoded_predictions_across_runs(tmp_path):
    """Determinism implies identical decoded predictions on the fixed eval set.

    This is the strongest form of the reproducibility guarantee (Req 11.3):
    identical seeds/config/data produce identical weights and therefore the same
    recognized text for every fixed eval image.
    """
    config = _tiny_config()
    dataset = _synthetic_dataset(config)
    eval_set = _fixed_eval_set(config)

    settings_a = _test_settings(tmp_path / "run_a")
    settings_b = _test_settings(tmp_path / "run_b")

    result_a = train_base_model(
        dataset, config=config, max_epochs=3, settings=settings_a
    )
    result_b = train_base_model(
        dataset, config=config, max_epochs=3, settings=settings_b
    )

    preds_a = _predict(result_a.model, eval_set)
    preds_b = _predict(result_b.model, eval_set)

    assert preds_a == preds_b
