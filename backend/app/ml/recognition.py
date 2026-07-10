"""HTR inference engine: CTC decoding and result assembly (tasks 7.2, 7.3).

This module implements the third and fourth stages of the HTR_Engine pipeline
(Recognize + Assemble) described in the design. It:

1. Crops each word image (from the segmentation stage) to the target height,
   runs the crop through the CRNN model (optionally composed with a user
   adapter), and applies greedy CTC decoding.
2. Assembles the decoded words back into ordered lines → ordered words, each
   word carrying its source region coordinates and a confidence score.
3. Returns a ``RecognitionResult`` with a ``recognized_any_text`` flag.

The interface matches the design specification exactly:

    recognize(image, adapter) -> RecognitionResult

    RecognitionResult = {
        lines: [{ index, region, words: [{ index, text, region, confidence }] }],
        recognized_any_text: bool,
    }

Requirements 2.1, 2.2, 2.4, 2.5 are addressed here:
- 2.1 / 2.2: every detected line and word receives recognized text.
- 2.4: every recognized word carries source region coordinates.
- 2.5: ``recognized_any_text`` is False when nothing was decoded.

Adapter Composition (Req 5.4, 5.5)
-----------------------------------
When a user has an active adapter, its weights are composed with the frozen
base model for that inference. The adapter stores a ``state_dict`` containing
only the CTC classifier layer's parameters (``weight`` and optionally ``bias``).
Composition is **last-layer substitution**: the base model is cloned and the
classifier's weight/bias are replaced with the adapter's values, leaving all
other (frozen) parameters unchanged.

When no adapter is provided, the base model alone is used.

CTC Decoding
------------
Greedy decoding:

1. Take the argmax class at each timestep.
2. Collapse runs of the same class to a single occurrence.
3. Remove any remaining blank tokens (index 0, ``BLANK_INDEX``).
4. Map each remaining index to its character in ``ALPHABET``
   (character at position ``i`` maps to ``ALPHABET[i - 1]`` because blank
   occupies index 0 and alphabet characters start at index 1).

The result is the most-likely label sequence per CTC semantics.
"""

from __future__ import annotations

import copy
import io
import logging
import threading
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import torch

from app.ml.htr_config import ALPHABET, BLANK_INDEX, DEFAULT_HTR_CONFIG, HTRModelConfig
from app.ml.htr_model import CRNN, build_model
from app.ml.preprocessing import PreprocessedImage, preprocess
from app.ml.segmentation import (
    LineSegment,
    Region,
    SegmentationResult,
    WordSegment,
    segment,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecognizedWord:
    """A single recognized word with its source region.

    Attributes
    ----------
    index:
        Left-to-right position of this word within its containing line.
    text:
        Decoded text string (may be empty for images with no decodable ink).
    region:
        Source bounding box in the original image's pixel coordinate space.
    confidence:
        Scalar confidence in ``[0, 1]``; derived from the mean log-probability
        at the chosen argmax steps (higher = more confident).
    """

    index: int
    text: str
    region: Region
    confidence: float


@dataclass(frozen=True)
class RecognizedLine:
    """A recognized text line: ordered words, with the line's region.

    Attributes
    ----------
    index:
        Top-to-bottom position of this line in the document.
    region:
        Bounding box of the line in image pixel coordinates.
    text:
        Full decoded line transcription before it is distributed across word
        boxes. This preserves the model's actual line-level output when token
        count and detected region count do not align cleanly.
    words:
        Left-to-right ordered list of :class:`RecognizedWord` objects.
    """

    index: int
    region: Region
    text: str
    words: List[RecognizedWord]


@dataclass(frozen=True)
class RecognitionResult:
    """The complete recognition output for one image.

    Attributes
    ----------
    lines:
        Top-to-bottom ordered list of :class:`RecognizedLine` objects.
    recognized_any_text:
        ``True`` if at least one non-empty word was recognized; ``False`` when
        the image contains no detectable text or every crop decoded to an empty
        string. (Req 2.5)
    image_width / image_height:
        Pixel dimensions of the preprocessed image whose coordinate space owns
        every returned region.
    """

    lines: List[RecognizedLine]
    recognized_any_text: bool
    image_width: int = 0
    image_height: int = 0


# ---------------------------------------------------------------------------
# Shared Base_Model singleton (design: "loaded once and shared across all
# requests" — Architectural Style, Frozen base + pluggable adapter)
# ---------------------------------------------------------------------------

_base_model_lock = threading.Lock()
_base_model_cache: CRNN | None = None


def get_base_model(*, force_reload: bool = False) -> CRNN:
    """Return the process-wide shared, frozen ``Base_Model``.

    Loads the trained artifact written by ``python -m app.ml.train_base`` (see
    :func:`app.ml.train_base.load_base_model`) the first time it is needed and
    caches it for the lifetime of the process, so every recognition request
    (and the personalization worker) composes adapters on top of the exact
    same weights rather than a freshly random-initialised model per call.

    When no trained artifact exists yet (e.g. a clean checkout before the
    documented training procedure has been run), falls back to a freshly
    built, frozen model with the pinned default configuration so recognition
    remains functional — just without any learned weights — rather than
    failing outright. This fallback is itself cached, so all calls within the
    process still share one consistent (if untrained) model.

    Parameters
    ----------
    force_reload:
        When ``True``, bypasses the cache and reloads from disk. Primarily
        useful for tests/tools that (re)train a base model artifact within a
        long-lived process.
    """
    global _base_model_cache

    if _base_model_cache is not None and not force_reload:
        return _base_model_cache

    with _base_model_lock:
        if _base_model_cache is not None and not force_reload:
            return _base_model_cache

        # Imported lazily to avoid a circular import (train_base imports
        # htr_model, not recognition).
        from app.ml.train_base import default_model_dir, load_base_model

        model_dir = default_model_dir()
        weights_path = model_dir / "base_model.pt"

        if weights_path.exists():
            try:
                _base_model_cache = load_base_model(model_dir)
                logger.info("Loaded shared base model artifact from %s", weights_path)
                return _base_model_cache
            except Exception:
                logger.exception(
                    "Failed to load base model artifact at %s; falling back to "
                    "a freshly initialised model.",
                    weights_path,
                )

        from app.ml.train_base import freeze_base_model

        _base_model_cache = freeze_base_model(build_model(DEFAULT_HTR_CONFIG))
        logger.warning(
            "No trained base model artifact found at %s; using an "
            "untrained (randomly initialised) frozen model. Run "
            "`python -m app.ml.train_base` to produce a real artifact.",
            weights_path,
        )
        return _base_model_cache


# ---------------------------------------------------------------------------
# Adapter composition (Req 5.4, 5.5)
# ---------------------------------------------------------------------------


def apply_adapter(base_model: CRNN, adapter_weights: bytes) -> CRNN:
    """Compose adapter weights with the frozen base model for one inference.

    Returns a *new* ``CRNN`` instance whose classifier layer has been replaced
    with the adapter's weights.  The base model is never mutated.

    Adapter format
    --------------
    The adapter weight file is a ``torch.save``-serialised ``dict`` that may
    contain any subset of the following keys:

    * ``"classifier.weight"`` — the full weight tensor for the linear CTC head
      (shape ``[num_classes_with_blank, lstm_output_size]``).
    * ``"classifier.bias"`` — optional bias tensor
      (shape ``[num_classes_with_blank]``).

    Any key that is absent is left unchanged from the base model.  Only the
    classifier layer is touched; all CNN and LSTM parameters remain frozen.

    Parameters
    ----------
    base_model:
        The shared, frozen base CRNN.  It is never modified in place.
    adapter_weights:
        Raw bytes produced by ``torch.save`` of the adapter ``state_dict``.

    Returns
    -------
    CRNN
        A shallow copy of the base model with the classifier weights replaced.
    """
    # Deep-copy so the base model is never mutated.
    adapted = copy.deepcopy(base_model)

    # Load the adapter state_dict from raw bytes (CPU-only; adapters are small).
    buf = io.BytesIO(adapter_weights)
    adapter_state: dict = torch.load(buf, map_location="cpu", weights_only=True)

    if not isinstance(adapter_state, dict):
        raise ValueError(
            f"Adapter weights must be a dict, got {type(adapter_state).__name__!r}."
        )

    # Apply only the keys that are present in the adapter.
    with torch.no_grad():
        if "classifier.weight" in adapter_state:
            w = adapter_state["classifier.weight"]
            if adapted.classifier.weight.shape != w.shape:
                raise ValueError(
                    f"Adapter classifier.weight shape {tuple(w.shape)} does not "
                    f"match base model shape {tuple(adapted.classifier.weight.shape)}."
                )
            adapted.classifier.weight.copy_(w)

        if "classifier.bias" in adapter_state:
            b = adapter_state["classifier.bias"]
            if adapted.classifier.bias is None:
                raise ValueError(
                    "Adapter provides classifier.bias but base model has no bias."
                )
            if adapted.classifier.bias.shape != b.shape:
                raise ValueError(
                    f"Adapter classifier.bias shape {tuple(b.shape)} does not "
                    f"match base model shape {tuple(adapted.classifier.bias.shape)}."
                )
            adapted.classifier.bias.copy_(b)

    return adapted


def _compose_model(base_model: CRNN, adapter: object | None) -> CRNN:
    """Return a model ready for inference — composed with the adapter when one is given.

    Parameters
    ----------
    base_model:
        The shared frozen base ``CRNN``.
    adapter:
        An object with a ``weights_path`` attribute (the ORM ``Adapter`` model),
        or ``None``.  When ``None``, the base model is used directly (Req 5.5).
        When present, the bytes at ``weights_path`` are loaded and composed with
        the base model (Req 5.4).

    Returns
    -------
    CRNN
        Either ``base_model`` unchanged (no adapter) or a new adapted copy.
    """
    if adapter is None:
        return base_model

    weights_path: str = getattr(adapter, "weights_path", None)
    if not weights_path:
        # Adapter object present but no weights path — fall back to base.
        return base_model

    try:
        with open(weights_path, "rb") as fh:
            adapter_bytes = fh.read()
    except OSError as exc:
        raise OSError(
            f"Failed to load adapter weights from {weights_path!r}: {exc}"
        ) from exc

    return apply_adapter(base_model, adapter_bytes)


# ---------------------------------------------------------------------------
# Crop preprocessing for the model
# ---------------------------------------------------------------------------


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
# Recognition and assembly
# ---------------------------------------------------------------------------


def _recognize_word(
    word: WordSegment,
    model: CRNN,
) -> tuple[str, float]:
    """Run one word crop through the model and return (text, confidence).

    The crop is resized to the model's expected ``image_height`` and fed
    through the CRNN. The output log-probabilities are greedily decoded.
    """
    target_height = model.config.image_height
    tensor = _prepare_crop(word.crop, target_height)

    model.eval()
    with torch.no_grad():
        log_probs = model(tensor)  # (T, 1, C)

    # Squeeze batch: (T, C)
    log_probs_single = log_probs.squeeze(1)
    return greedy_ctc_decode(log_probs_single, blank_index=model.config.blank_index)


def _crop_region(normalized: np.ndarray, region: Region) -> np.ndarray:
    """Extract a normalised float crop for ``region`` from a page image."""
    return normalized[region.y : region.bottom, region.x : region.right]


def _distribute_line_text(line_text: str, num_words: int) -> list[str]:
    """Split a line transcription across ``num_words`` segmented word slots.

    The base model is trained on IAM *line* images, so inference recognises one
    string per line and we map tokens back onto the word regions the layout
    stage already found (left-to-right).
    """
    if num_words <= 0:
        return []
    tokens = line_text.split()
    if not tokens:
        return [""] * num_words
    if len(tokens) == num_words:
        return tokens
    if len(tokens) < num_words:
        return tokens + [""] * (num_words - len(tokens))
    # More CTC tokens than word boxes: keep early alignment and fold the tail
    # into the last slot so nothing is dropped from the line transcript.
    head = tokens[: num_words - 1]
    tail = " ".join(tokens[num_words - 1 :])
    return head + [tail]


def assemble_result(
    seg_result: SegmentationResult,
    model: CRNN,
    *,
    normalized: np.ndarray | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> RecognitionResult:
    """Run crops through the model and assemble the ordered result.

    When ``normalized`` is supplied (the usual path from :func:`recognize`), each
    *line* is recognised as a single crop — matching how the shared base model
    is trained on IAM line images. The decoded string is then split across the
    word regions already detected for that line. When ``normalized`` is omitted,
    each word crop is recognised independently (legacy/test behaviour).

    Requirements 2.1, 2.2, 2.4, 2.5 are all satisfied here:
    - 2.1 / 2.2: every line and word gets recognized text.
    - 2.4: every ``RecognizedWord`` carries the source ``Region``.
    - 2.5: ``recognized_any_text`` is ``False`` when nothing is detected.
    """
    recognized_lines: list[RecognizedLine] = []
    any_text = False

    for idx, line in enumerate(seg_result.lines):
        if progress_callback is not None:
            progress_callback(idx, len(seg_result.lines))

        recognized_words: list[RecognizedWord] = []

        if normalized is not None and line.words:
            line_crop = _crop_region(normalized, line.region)
            line_segment = WordSegment(
                index=0,
                region=Region(0, 0, line.region.width, line.region.height),
                crop=line_crop,
            )
            line_text, line_confidence = _recognize_word(line_segment, model)
            if line_text:
                any_text = True
            word_texts = _distribute_line_text(line_text, len(line.words))
            for word, text in zip(line.words, word_texts):
                recognized_words.append(
                    RecognizedWord(
                        index=word.index,
                        text=text,
                        region=word.region,
                        confidence=line_confidence if text else 0.0,
                    )
                )
        else:
            line_texts: list[str] = []
            for word in line.words:
                text, confidence = _recognize_word(word, model)
                if text:
                    any_text = True
                line_texts.append(text)
                recognized_words.append(
                    RecognizedWord(
                        index=word.index,
                        text=text,
                        region=word.region,
                        confidence=confidence,
                    )
                )
            line_text = " ".join(t for t in line_texts if t)

        recognized_lines.append(
            RecognizedLine(
                index=line.index,
                region=line.region,
                text=line_text,
                words=recognized_words,
            )
        )

    return RecognitionResult(
        lines=recognized_lines,
        recognized_any_text=any_text,
        image_width=seg_result.image_width,
        image_height=seg_result.image_height,
    )


# ---------------------------------------------------------------------------
# Public interface: recognize()
# ---------------------------------------------------------------------------


def recognize(
    image: PreprocessedImage,
    adapter: object | None = None,
    model: CRNN | None = None,
    config: HTRModelConfig | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> RecognitionResult:
    """Recognize handwritten text in a preprocessed image.

    This is the main public entry point for the HTR_Engine inference stage.
    It chains segmentation (layout analysis) → word-level CRNN inference →
    CTC decoding → result assembly.

    Parameters
    ----------
    image:
        A :class:`~app.ml.preprocessing.PreprocessedImage` from the
        preprocessing stage. The coordinate space of all returned regions is
        the pixel space of this image.
    adapter:
        Optional per-user adapter. Currently reserved for task 7.3 (adapter
        composition). Passing a non-None value is accepted but produces the
        same result as the base model until adapter composition is wired in.
    model:
        The CRNN model to use for inference. If ``None``, a freshly built
        model with default configuration is used. In production the model is
        loaded once at engine startup and injected here.
    config:
        HTR model configuration used when ``model`` is ``None``. Defaults to
        :data:`~app.ml.htr_config.DEFAULT_HTR_CONFIG`.

    Returns
    -------
    RecognitionResult
        Ordered lines → ordered words with source region coordinates and
        ``recognized_any_text`` flag.
    """
    from app.ml.segmentation import SegmentationConfig

    if model is None:
        # No explicit model/config override: use the shared, process-wide
        # frozen Base_Model so repeated calls are consistent (Req: base model
        # is loaded once and shared across all requests). Callers that pass an
        # explicit ``config`` are asking for a specific (e.g. tiny test)
        # architecture, so honour that instead of the singleton.
        model = get_base_model() if config is None else build_model(config)

    # Compose adapter with the base model when one is provided (Req 5.4, 5.5).
    inference_model = _compose_model(model, adapter)

    seg_result = segment(image)
    return assemble_result(
        seg_result, 
        inference_model, 
        normalized=image.normalized,
        progress_callback=progress_callback
    )


def recognize_bytes(
    image_bytes: bytes,
    adapter: object | None = None,
    model: CRNN | None = None,
    config: HTRModelConfig | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> RecognitionResult:
    """Convenience wrapper: decode + preprocess raw image bytes, then recognize.

    Raises
    ------
    app.ml.preprocessing.ImageDecodeError
        If ``image_bytes`` cannot be decoded as an image.
    """
    from app.ml.preprocessing import preprocess

    preprocessed = preprocess(image_bytes)
    return recognize(
        preprocessed, 
        adapter=adapter, 
        model=model, 
        config=config,
        progress_callback=progress_callback
    )


def recognize_crop(image_bytes: bytes, model: CRNN) -> str:
    """Recognize a single already-cropped word/line image and return its text.

    Unlike :func:`recognize_bytes`, this skips layout segmentation entirely —
    the caller (e.g. the Personalization_Service's evaluation step) supplies a
    crop that is already a single word or line region, such as a
    ``TrainingPair.region_crop_path`` image or a synthetic evaluation sample.
    The crop is decoded, resized to the model's expected height, and run
    through the CRNN + greedy CTC decoder.

    Returns an empty string if the crop cannot be decoded or recognized
    (callers evaluating CER/WER treat an empty hypothesis as a normal, if
    inaccurate, prediction rather than an error).
    """
    try:
        preprocessed = preprocess(image_bytes)
    except Exception:
        return ""

    try:
        region = Region(0, 0, preprocessed.width, preprocessed.height)
        word = WordSegment(index=0, region=region, crop=preprocessed.normalized)
        text, _confidence = _recognize_word(word, model)
        return text
    except Exception:
        return ""


__all__ = [
    "RecognizedWord",
    "RecognizedLine",
    "RecognitionResult",
    "greedy_ctc_decode",
    "assemble_result",
    "apply_adapter",
    "get_base_model",
    "recognize",
    "recognize_bytes",
    "recognize_crop",
]
