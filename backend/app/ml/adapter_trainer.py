"""PEFT adapter trainer with early stopping for per-user handwriting personalization.

This module implements ``train_adapter``, the parameter-efficient fine-tuning
procedure that adapts a shared frozen base model to a specific user's
handwriting (Requirements 5.2, 5.3, 6.5).

Design
------
The "adapter" is a **last-layer weight substitution**: only the CTC classifier
``nn.Linear`` layer is trained; the CNN feature extractor and BiLSTM sequence
model remain entirely frozen throughout.  This keeps the adapter small (just
``weight`` and ``bias`` of the classifier — a fraction of the total model
parameters) and is exactly the form consumed by
:func:`app.ml.recognition.apply_adapter`.

Early stopping (Req 6.5)
-------------------------
After each training epoch the adapter is evaluated on the held-out set by
computing the Character Error Rate (CER). Training stops as soon as:

    1. The held-out CER has *not* improved by at least ``min_cer_improvement``
       (default 0.5 pp = 0.005) for ``patience`` (default 3) consecutive
       epochs, **or**
    2. The epoch count reaches ``max_epochs`` (default 50).

Improvement is measured relative to the best CER seen so far:
``improvement = best_cer - current_cer``.  The run stops *after* the cycle in
which the patience counter first reaches its limit — meaning the stopping epoch
is the last epoch that failed to improve, not the first.

Determinism (Req 11.3)
-----------------------
The fixed ``seed`` argument controls all RNGs (Python, NumPy, PyTorch) before
any stochastic operation (weight init copy, data shuffling).

One adapter per user (Req 5.3)
-------------------------------
``train_adapter`` returns an :class:`AdapterResult` carrying the new classifier
weight and bias tensors.  The caller (the Personalization_Service or its async
worker) is responsible for persisting the result and overwriting any existing
adapter for the user — this module produces the artifact but does not touch the
database or filesystem directly.

Base model isolation
---------------------
``train_adapter`` never modifies ``base_model``.  It deep-copies the classifier
parameters into a **new** ``nn.Linear`` that is the only trainable component,
leaving the base model — including its ``requires_grad=False`` parameters —
completely unchanged.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field

import numpy as np
import torch
from torch import Tensor, nn
from torch.utils.data import DataLoader, Dataset
import torchvision.transforms.v2 as transforms

from app.ml.htr_config import ALPHABET
from app.ml.htr_model import CRNN
from app.ml.train_base import LabeledSample, _collate, _InputLengthCache, encode_text

# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------

_CHAR_TO_INDEX: dict[str, int] = {ch: i + 1 for i, ch in enumerate(ALPHABET)}


@dataclass(frozen=True)
class AdapterResult:
    """The trained adapter artifact returned by :func:`train_adapter`.

    Attributes
    ----------
    classifier_weight:
        Tensor of shape ``(num_classes_with_blank, lstm_output_size)`` — the
        fine-tuned weight matrix of the CTC classifier head.
    classifier_bias:
        Tensor of shape ``(num_classes_with_blank,)`` — the fine-tuned bias
        vector. Always present (the base CRNN always has a bias).
    epochs_run:
        Number of full training epochs completed before stopping, in
        ``[1, max_epochs]``.
    cer_history:
        Per-epoch held-out CER values. Length equals ``epochs_run``.
    """

    classifier_weight: Tensor
    classifier_bias: Tensor
    epochs_run: int
    cer_history: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CER helper (character-level Levenshtein / len(reference))
# ---------------------------------------------------------------------------


def _levenshtein_char(reference: str, hypothesis: str) -> int:
    """Compute the character-level Levenshtein edit distance."""
    r, h = list(reference), list(hypothesis)
    nr, nh = len(r), len(h)

    # dp[i][j] = edit distance between r[:i] and h[:j]
    dp = list(range(nh + 1))
    for i in range(1, nr + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, nh + 1):
            temp = dp[j]
            if r[i - 1] == h[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = temp
    return dp[nh]


def compute_cer(reference: str, hypothesis: str) -> float:
    """Character Error Rate = Levenshtein(ref, hyp) / len(ref).

    Returns 0.0 when both strings are empty. Returns ``inf`` only when the
    reference is empty but the hypothesis is non-empty (unavailable case;
    callers should guard against this).
    """
    if not reference and not hypothesis:
        return 0.0
    if not reference:
        # No reference characters — undefined; return inf so callers can guard.
        return float("inf")
    return _levenshtein_char(reference, hypothesis) / len(reference)


# ---------------------------------------------------------------------------
# Greedy CTC decoder (minimal, for evaluation only)
# ---------------------------------------------------------------------------


def _greedy_decode(log_probs: Tensor, blank_index: int = 0) -> str:
    """Greedy CTC decode: argmax → collapse runs → remove blanks → text."""
    # log_probs: (T, 1, C) or (T, C)
    if log_probs.dim() == 3:
        log_probs = log_probs.squeeze(1)  # (T, C)
    indices = torch.argmax(log_probs, dim=1).tolist()  # (T,)

    # Collapse consecutive duplicates, remove blanks.
    chars: list[str] = []
    prev: int | None = None
    for idx in indices:
        if idx != prev:
            prev = idx
            if idx != blank_index and 0 < idx <= len(ALPHABET):
                chars.append(ALPHABET[idx - 1])
    return "".join(chars)


# ---------------------------------------------------------------------------
# Held-out CER evaluation
# ---------------------------------------------------------------------------


def _evaluate_cer(
    classifier: nn.Linear,
    base_model: CRNN,
    held_out_pairs: list[LabeledSample],
) -> float:
    """Compute mean CER on ``held_out_pairs`` using ``classifier`` as the head.

    The base model's CNN + LSTM are used as the feature extractor (frozen);
    ``classifier`` is the adapter's fine-tuned head.

    Returns 0.0 if the held-out set is empty.
    """
    if not held_out_pairs:
        return 0.0

    base_model.eval()
    classifier.eval()
    blank_index = base_model.config.blank_index

    total_cer = 0.0
    count = 0

    with torch.no_grad():
        for sample in held_out_pairs:
            # Shape: (1, C, H, W)
            image = sample.image.unsqueeze(0)

            # Feature extraction via frozen CNN + LSTM.
            feats = base_model.cnn(image)      # (1, C_out, 1, W)
            feats = feats.squeeze(2)           # (1, C_out, W)
            feats = feats.permute(2, 0, 1)     # (W=T, 1, C_out)
            seq, _ = base_model.lstm(feats)    # (T, 1, lstm_out)

            # Adapter classifier head.
            logits = classifier(seq)            # (T, 1, num_classes)
            log_probs = nn.functional.log_softmax(logits, dim=2)  # (T, 1, C)

            hypothesis = _greedy_decode(log_probs, blank_index=blank_index)
            cer = compute_cer(sample.text, hypothesis)
            if not (cer == float("inf")):
                total_cer += cer
                count += 1

    return total_cer / max(count, 1)


# ---------------------------------------------------------------------------
# Adapter dataset wrapper
# ---------------------------------------------------------------------------


class _AdapterDataset(Dataset):
    """Thin Dataset wrapper around a list of :class:`LabeledSample`.
    
    Optionally applies data augmentation to the images.
    """

    def __init__(self, samples: list[LabeledSample], is_train: bool = False) -> None:
        self._samples = samples
        self.is_train = is_train
        if self.is_train:
            self.transform = transforms.Compose([
                transforms.RandomRotation(degrees=5, fill=1.0),
                transforms.ElasticTransform(alpha=10.0, sigma=2.0),
                transforms.GaussianBlur(kernel_size=3, sigma=(0.1, 2.0))
            ])
        else:
            self.transform = None

    def __len__(self) -> int:
        return len(self._samples)

    def __getitem__(self, index: int) -> LabeledSample:
        sample = self._samples[index]
        if self.transform is not None:
            image = self.transform(sample.image)
            return LabeledSample(image=image, text=sample.text)
        return sample


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def train_adapter(
    base_model: CRNN,
    training_pairs: list[LabeledSample],
    held_out_pairs: list[LabeledSample],
    *,
    seed: int = 42,
    max_epochs: int = 50,
    min_cer_improvement: float = 0.005,
    patience: int = 3,
    learning_rate: float = 1e-3,
    batch_size: int = 16,
) -> AdapterResult:
    """Train a PEFT adapter (last-layer fine-tune) for a single user.

    Only the CTC classifier layer (``base_model.classifier``) is trained.
    All CNN and LSTM parameters in ``base_model`` are never touched.
    ``base_model`` itself is never modified — this function deep-copies the
    classifier weights into a fresh ``nn.Linear`` that becomes the only
    trainable component.

    Parameters
    ----------
    base_model:
        Frozen CRNN base model.  Its CNN and LSTM provide feature extraction;
        its classifier layer provides the starting point for the adapter.
        **Never mutated.**
    training_pairs:
        Labeled samples used for gradient updates.
    held_out_pairs:
        Labeled samples used only for early-stopping CER evaluation; excluded
        from all gradient updates.
    seed:
        Integer random seed for reproducibility.  Controls Python, NumPy, and
        PyTorch RNGs before any stochastic operation.
    max_epochs:
        Hard epoch cap (Req 6.5: ≤ 50).  Default: 50.
    min_cer_improvement:
        Minimum CER decrease (absolute, in proportion units where 1.0 = 100%)
        to count as an improvement. Default: 0.005 (0.5 pp) per Req 6.5.
    patience:
        Number of consecutive non-improving epochs before early stopping.
        Default: 3 (Req 6.5).
    learning_rate:
        Learning rate for the Adam optimizer on adapter parameters.
    batch_size:
        Mini-batch size for training; capped at ``len(training_pairs)``.

    Returns
    -------
    AdapterResult
        Contains the fine-tuned ``classifier_weight`` and ``classifier_bias``
        tensors (same shape as the base model's classifier), the number of
        epochs run, and the per-epoch held-out CER history.

    Raises
    ------
    ValueError
        If ``training_pairs`` is empty.
    """
    if not training_pairs:
        raise ValueError("training_pairs must not be empty")

    # ------------------------------------------------------------------
    # 1. Fix all RNGs before any stochastic operation.
    # ------------------------------------------------------------------
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)

    # ------------------------------------------------------------------
    # 2. Deep-copy the classifier into an independent trainable module.
    #    The base_model is never modified.
    # ------------------------------------------------------------------
    base_model.eval()
    for param in base_model.parameters():
        param.requires_grad_(False)

    adapter_classifier = copy.deepcopy(base_model.classifier)
    for param in adapter_classifier.parameters():
        param.requires_grad_(True)

    # ------------------------------------------------------------------
    # 3. Build data loader with seeded generator.
    # ------------------------------------------------------------------
    effective_batch_size = min(batch_size, len(training_pairs))
    generator = torch.Generator()
    generator.manual_seed(seed)

    loader = DataLoader(
        _AdapterDataset(training_pairs, is_train=True),
        batch_size=effective_batch_size,
        shuffle=True,
        generator=generator,
        num_workers=0,
        collate_fn=_collate,
        drop_last=False,
    )

    optimizer = torch.optim.Adam(
        adapter_classifier.parameters(),
        lr=learning_rate,
    )
    ctc_loss = nn.CTCLoss(
        blank=base_model.config.blank_index,
        zero_infinity=True,
    )
    length_cache = _InputLengthCache(base_model)

    # ------------------------------------------------------------------
    # 4. Training loop with early stopping.
    # ------------------------------------------------------------------
    best_cer: float | None = None
    no_improvement_count: int = 0
    cer_history: list[float] = []
    epochs_run: int = 0

    for _epoch in range(max_epochs):
        epochs_run += 1
        adapter_classifier.train()

        for images, widths, targets, target_lengths in loader:
            optimizer.zero_grad()

            # Forward through frozen CNN + LSTM.
            with torch.no_grad():
                feats = base_model.cnn(images)  # (N, C, 1, W)
                feats = feats.squeeze(2)         # (N, C, W)
                feats = feats.permute(2, 0, 1)   # (T, N, C)
                seq, _ = base_model.lstm(feats)  # (T, N, lstm_out)

            # Forward through trainable classifier.
            logits = adapter_classifier(seq)                          # (T, N, classes)
            log_probs = nn.functional.log_softmax(logits, dim=2)     # (T, N, classes)

            t_full = log_probs.size(0)
            input_lengths = torch.tensor(
                [min(length_cache.of(w), t_full) for w in widths],
                dtype=torch.long,
            )

            loss = ctc_loss(log_probs, targets, input_lengths, target_lengths)
            loss.backward()
            nn.utils.clip_grad_norm_(adapter_classifier.parameters(), max_norm=5.0)
            optimizer.step()

        # ------------------------------------------------------------------
        # 5. Held-out CER evaluation for early stopping.
        # ------------------------------------------------------------------
        epoch_cer = _evaluate_cer(adapter_classifier, base_model, held_out_pairs)
        cer_history.append(epoch_cer)

        if best_cer is None:
            # First epoch: initialise best; no patience counting yet.
            best_cer = epoch_cer
            no_improvement_count = 0
        else:
            improvement = best_cer - epoch_cer
            if improvement >= min_cer_improvement:
                best_cer = epoch_cer
                no_improvement_count = 0
            else:
                no_improvement_count += 1
                if no_improvement_count >= patience:
                    # Req 6.5: stop when CER hasn't improved by ≥0.5 pp
                    # for `patience` consecutive cycles.
                    break

    # ------------------------------------------------------------------
    # 6. Return the adapter weights (detached tensors, no grad).
    # ------------------------------------------------------------------
    adapter_classifier.eval()
    with torch.no_grad():
        final_weight = adapter_classifier.weight.detach().clone()
        final_bias = adapter_classifier.bias.detach().clone()  # type: ignore[union-attr]

    return AdapterResult(
        classifier_weight=final_weight,
        classifier_bias=final_bias,
        epochs_run=epochs_run,
        cer_history=cer_history,
    )


# ---------------------------------------------------------------------------
# Adapter serialisation helpers
# ---------------------------------------------------------------------------


def adapter_result_to_bytes(result: AdapterResult) -> bytes:
    """Serialise an :class:`AdapterResult` to bytes via ``torch.save``.

    The produced bytes match the format expected by
    :func:`app.ml.recognition.apply_adapter`: a ``dict`` with keys
    ``"classifier.weight"`` and ``"classifier.bias"``.
    """
    import io

    buf = io.BytesIO()
    torch.save(
        {
            "classifier.weight": result.classifier_weight,
            "classifier.bias": result.classifier_bias,
        },
        buf,
    )
    return buf.getvalue()


__all__ = [
    "AdapterResult",
    "compute_cer",
    "train_adapter",
    "adapter_result_to_bytes",
]
