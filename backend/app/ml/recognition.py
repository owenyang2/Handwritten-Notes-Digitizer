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
from typing import Callable, List, Optional

import numpy as np
import torch

from app.ml.decode import _prepare_crop, greedy_ctc_decode
from app.ml.htr_config import DEFAULT_HTR_CONFIG, HTRModelConfig
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

_base_model_lock = threading.RLock()
_base_model_cache: CRNN | None = None
_loaded_model_status: "LoadedModelStatus | None" = None


class ModelUnavailableError(RuntimeError):
    """Raised when a trained, probed base-model artifact cannot be used."""


@dataclass(frozen=True)
class LoadedModelStatus:
    """Runtime identity of the model actually loaded for inference."""

    loaded: bool
    backend: str = "crnn-ctc"
    artifact_path: str | None = None
    reason: str | None = None
    probe_ok: bool = False

    @property
    def health_label(self) -> str:
        return "loaded" if self.loaded and self.probe_ok else "unavailable"


def _probe_crnn(model: CRNN) -> bool:
    """Run a tiny forward pass so a corrupt checkpoint cannot look healthy."""
    model.eval()
    height = model.config.image_height
    dummy = torch.zeros(1, model.config.input_channels, height, max(32, height))
    with torch.no_grad():
        output = model(dummy)
    return (
        output.ndim == 3
        and output.size(1) == 1
        and output.size(-1) == model.config.num_classes
        and torch.isfinite(output).all().item()
    )


def reset_base_model_runtime() -> None:
    """Drop the process-wide model cache. Used by tests and after failed loads."""
    global _base_model_cache, _loaded_model_status
    with _base_model_lock:
        _base_model_cache = None
        _loaded_model_status = None


def get_loaded_model_status(*, force_reload: bool = False) -> LoadedModelStatus:
    """Return the cached load/probe result without raising."""
    global _loaded_model_status, _base_model_cache

    if _loaded_model_status is not None and not force_reload:
        return _loaded_model_status

    with _base_model_lock:
        if _loaded_model_status is not None and not force_reload:
            return _loaded_model_status
        try:
            get_base_model(force_reload=True)
        except ModelUnavailableError as exc:
            # ``get_base_model`` records the precise artifact path and failure
            # stage before raising. Preserve that identity in health output;
            # replacing it with a generic status would make a missing/corrupt
            # model look indistinguishable from an unconfigured backend.
            if _loaded_model_status is None:
                _loaded_model_status = LoadedModelStatus(
                    loaded=False,
                    reason=str(exc),
                    probe_ok=False,
                )
        return _loaded_model_status or LoadedModelStatus(
            loaded=False,
            reason="Base model status has not been determined.",
            probe_ok=False,
        )


def is_base_model_ready() -> bool:
    status = get_loaded_model_status()
    return status.loaded and status.probe_ok


def get_base_model(*, force_reload: bool = False) -> CRNN:
    """Return the process-wide shared, frozen ``Base_Model``.

    Loads the trained artifact written by ``python -m app.ml.train_base`` (see
    :func:`app.ml.train_base.load_base_model`) the first time it is needed and
    caches it for the lifetime of the process, so every recognition request
    (and the personalization worker) composes adapters on top of the exact
    same weights rather than a freshly random-initialised model per call.

    Missing, corrupt, or incompatible artifacts raise
    :class:`ModelUnavailableError`. Recognition never silently falls back to
    random weights.
    """
    global _base_model_cache, _loaded_model_status

    if _base_model_cache is not None and not force_reload:
        return _base_model_cache

    with _base_model_lock:
        if _base_model_cache is not None and not force_reload:
            return _base_model_cache

        from app.ml.train_base import default_model_dir, load_base_model

        model_dir = default_model_dir()
        weights_path = model_dir / "base_model.pt"
        path_str = str(weights_path)

        if not weights_path.exists():
            _base_model_cache = None
            _loaded_model_status = LoadedModelStatus(
                loaded=False,
                artifact_path=path_str,
                reason=f"No trained base model artifact found at {path_str}.",
                probe_ok=False,
            )
            raise ModelUnavailableError(_loaded_model_status.reason)

        try:
            model = load_base_model(model_dir)
        except Exception as exc:
            logger.exception("Failed to load base model artifact at %s.", weights_path)
            _base_model_cache = None
            _loaded_model_status = LoadedModelStatus(
                loaded=False,
                artifact_path=path_str,
                reason=f"Base model artifact at {path_str} could not be loaded: {exc}",
                probe_ok=False,
            )
            raise ModelUnavailableError(_loaded_model_status.reason) from exc

        try:
            probe_ok = _probe_crnn(model)
        except Exception as exc:
            logger.exception("Base model probe failed for %s.", weights_path)
            _base_model_cache = None
            _loaded_model_status = LoadedModelStatus(
                loaded=False,
                artifact_path=path_str,
                reason=f"Base model artifact at {path_str} failed its inference check: {exc}",
                probe_ok=False,
            )
            raise ModelUnavailableError(_loaded_model_status.reason) from exc

        if not probe_ok:
            _base_model_cache = None
            _loaded_model_status = LoadedModelStatus(
                loaded=False,
                artifact_path=path_str,
                reason=f"Base model artifact at {path_str} failed its inference check.",
                probe_ok=False,
            )
            raise ModelUnavailableError(_loaded_model_status.reason)

        _base_model_cache = model
        _loaded_model_status = LoadedModelStatus(
            loaded=True,
            artifact_path=path_str,
            reason=None,
            probe_ok=True,
        )
        logger.info("Loaded shared base model artifact from %s", weights_path)
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
        # frozen Base_Model so repeated calls are consistent. Callers that pass
        # an explicit ``config`` are asking for a specific (e.g. tiny test)
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
    "ModelUnavailableError",
    "LoadedModelStatus",
    "greedy_ctc_decode",
    "assemble_result",
    "apply_adapter",
    "get_base_model",
    "get_loaded_model_status",
    "reset_base_model_runtime",
    "is_base_model_ready",
    "recognize",
    "recognize_bytes",
    "recognize_crop",
]
