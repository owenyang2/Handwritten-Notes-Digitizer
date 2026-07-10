import torch
import numpy as np
from app.ml.htr_config import ALPHABET, BLANK_INDEX

def _prepare_crop(
    crop: np.ndarray,
    target_height: int,
) -> torch.Tensor:
    """Resize a word crop to ``target_height`` and return a model-ready tensor.

    The crop is expected to be a float32 array in ``[0, 1]`` of shape
    ``(H, W)``.  It is resized to ``(target_height, W')`` while preserving
    the aspect ratio, then converted to a ``(1, 1, target_height, W')``
    tensor suitable for :class:`~app.ml.htr_model.CRNN`.

    If the resulting width would be zero (tiny crop), a minimal 1-pixel-wide
    tensor is returned so the model still produces a prediction rather than
    crashing.
    """
    from PIL import Image  # local import; Pillow is a listed dependency

    h, w = crop.shape[:2]
    if h == 0 or w == 0:
        # Degenerate crop: return a blank tensor at minimum size.
        return torch.zeros(1, 1, target_height, max(w, 1), dtype=torch.float32)

    # Scale width proportionally.
    scale = target_height / h
    new_w = max(1, int(round(w * scale)))

    # Pillow expects uint8; we work in float32 [0,1] so convert both ways.
    uint8_crop = (crop * 255).clip(0, 255).astype(np.uint8)
    pil_img = Image.fromarray(uint8_crop, mode="L")
    pil_resized = pil_img.resize((new_w, target_height), Image.BILINEAR)
    resized = np.asarray(pil_resized, dtype=np.float32) / 255.0

    # Shape: (1, 1, H, W) — batch=1, channel=1, height, width.
    return torch.from_numpy(resized).unsqueeze(0).unsqueeze(0)


# ---------------------------------------------------------------------------
# Greedy CTC decoder
# ---------------------------------------------------------------------------




def greedy_ctc_decode(
    log_probs: torch.Tensor,
    blank_index: int = BLANK_INDEX,
) -> tuple[str, float]:
    """Greedy CTC decode a single sequence of log-probabilities.

    Parameters
    ----------
    log_probs:
        Tensor of shape ``(T, C)`` — CTC log-probabilities for one sample
        (time steps × classes). The class dimension must be
        ``len(ALPHABET) + 1``, with class 0 reserved for the blank token.
    blank_index:
        Index of the CTC blank class (default ``0`` per ``BLANK_INDEX``).

    Returns
    -------
    text:
        Decoded string (may be empty if all timesteps decode to blank).
    confidence:
        Mean probability mass at the chosen (argmax) class across *non-blank*
        timesteps that survived collapsing. Returns 0.0 if the decoded text is
        empty.

    Algorithm
    ---------
    1. Argmax across the class dimension at every timestep.
    2. Collapse consecutive identical indices to a single occurrence.
    3. Remove blank tokens.
    4. Map remaining indices to characters via ``ALPHABET`` (offset by 1
       because blank occupies index 0: character ``ALPHABET[i-1]`` for
       class index ``i``).
    """
    # log_probs: (T, C)
    if log_probs.dim() == 3:
        # Passed (T, N=1, C) shape — squeeze the batch dimension.
        if log_probs.size(1) != 1:
            raise ValueError(
                "greedy_ctc_decode expects a single sequence; "
                f"got batch size {log_probs.size(1)}"
            )
        log_probs = log_probs.squeeze(1)  # -> (T, C)

    best_indices = torch.argmax(log_probs, dim=1)  # (T,)
    best_log_probs = log_probs[torch.arange(len(best_indices)), best_indices]  # (T,)

    # Step 1: collapse consecutive duplicates.
    collapsed_indices: list[int] = []
    collapsed_log_probs: list[float] = []
    prev: int | None = None
    for idx, lp in zip(best_indices.tolist(), best_log_probs.tolist()):
        if idx != prev:
            collapsed_indices.append(int(idx))
            collapsed_log_probs.append(float(lp))
            prev = int(idx)

    # Step 2: remove blank tokens.
    char_indices: list[int] = []
    char_log_probs: list[float] = []
    for idx, lp in zip(collapsed_indices, collapsed_log_probs):
        if idx != blank_index:
            char_indices.append(idx)
            char_log_probs.append(lp)

    # Step 3: map indices to characters.
    # Class 0 = blank, class 1 = ALPHABET[0], class k = ALPHABET[k-1].
    chars: list[str] = []
    for idx in char_indices:
        alphabet_pos = idx - 1  # shift off the blank slot
        if 0 <= alphabet_pos < len(ALPHABET):
            chars.append(ALPHABET[alphabet_pos])
        # Out-of-range indices are silently skipped (should not occur in
        # practice with a well-formed model output).

    text = "".join(chars)

    # Step 4: compute confidence as mean probability at chosen steps.
    if char_log_probs:
        mean_log_prob = sum(char_log_probs) / len(char_log_probs)
        confidence = float(np.exp(mean_log_prob))
    else:
        confidence = 0.0

    return text, confidence


# ---------------------------------------------------------------------------
# Beam Search Decoder
# ---------------------------------------------------------------------------

_decoder = None

def _get_decoder():
    global _decoder
    if _decoder is None:
        from pyctcdecode import build_ctcdecoder
        labels = [""] + list(ALPHABET)
        _decoder = build_ctcdecoder(labels)
    return _decoder


def beam_search_decode(
    log_probs: torch.Tensor,
    beam_width: int = 10,
) -> tuple[str, float]:
    """Beam search CTC decode a single sequence of log-probabilities.

    Parameters
    ----------
    log_probs:
        Tensor of shape ``(T, C)`` — CTC log-probabilities for one sample.
    beam_width:
        Number of beams to keep during decoding.

    Returns
    -------
    text:
        Decoded string.
    confidence:
        Heuristic confidence score based on the chosen beam's score.
    """
    if log_probs.dim() == 3:
        if log_probs.size(1) != 1:
            raise ValueError(
                "beam_search_decode expects a single sequence; "
                f"got batch size {log_probs.size(1)}"
            )
        log_probs = log_probs.squeeze(1)  # -> (T, C)
        
    # Convert from log_probs (PyTorch tensor) to numpy array of probabilities or logits.
    # pyctcdecode works best with logits or log_probs directly, we just pass numpy.
    logits = log_probs.detach().cpu().numpy()
    
    decoder = _get_decoder()
    # Decode
    text = decoder.decode(logits, beam_width=beam_width)
    
    # Extract confidence (optional, pyctcdecode has more advanced metrics but this works)
    # We will use greedy confidence for simplicity as a fallback, or pyctcdecode's logit score
    # For now, let's just reuse the greedy confidence score on the best path to keep it aligned
    # with the existing application logic that expects a [0, 1] confidence.
    _, confidence = greedy_ctc_decode(log_probs)
    
    return text, confidence

# ---------------------------------------------------------------------------
# Recognition and assembly
# ---------------------------------------------------------------------------


