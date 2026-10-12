"""Training losses for the compact image-to-LaTeX model."""

from __future__ import annotations

import torch
from torch import Tensor, nn


def token_cross_entropy(
    logits: Tensor,
    targets: Tensor,
    *,
    pad_id: int,
) -> Tensor:
    """Mean CE over non-pad tokens, including EOS. Inputs are already shifted."""
    vocab = logits.size(-1)
    loss = nn.functional.cross_entropy(
        logits.reshape(-1, vocab),
        targets.reshape(-1),
        ignore_index=pad_id,
        reduction="sum",
    )
    valid = (targets != pad_id).sum().clamp(min=1)
    return loss / valid
