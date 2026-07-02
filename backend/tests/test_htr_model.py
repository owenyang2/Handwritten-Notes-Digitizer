"""Unit tests for the CNN + BiLSTM + CTC base-model architecture (task 6.1).

These tests verify that the model builds from its pinned configuration and that
a forward pass over a dummy line image produces CTC log-probabilities of the
expected ``(T, N, C)`` shape, where ``C == num_classes + 1`` (alphabet + blank).
"""

import math

import torch

from app.ml.htr_config import (
    ALPHABET,
    DEFAULT_HTR_CONFIG,
    HTRModelConfig,
    TrainingConfig,
)
from app.ml.htr_model import CRNN, build_model


def _dummy_batch(config: HTRModelConfig, batch: int, width: int) -> torch.Tensor:
    return torch.randn(batch, config.input_channels, config.image_height, width)


def test_model_builds_from_default_config():
    model = build_model()
    assert isinstance(model, CRNN)
    # Pinned defaults are wired through to the module.
    assert model.config is DEFAULT_HTR_CONFIG


def test_forward_produces_ctc_log_probs_of_expected_shape():
    config = DEFAULT_HTR_CONFIG
    model = build_model(config).eval()
    batch, width = 4, 256

    with torch.no_grad():
        out = model(_dummy_batch(config, batch, width))

    # CTC layout is (T, N, C).
    assert out.dim() == 3
    t, n, c = out.shape
    assert n == batch
    # Classes = alphabet + 1 blank.
    assert c == config.num_classes_with_blank == len(ALPHABET) + 1
    # Time axis is the downsampled width and must be positive.
    assert t == model.output_length(width)
    assert t > 0


def test_output_is_valid_log_probability_distribution():
    config = DEFAULT_HTR_CONFIG
    model = build_model(config).eval()

    with torch.no_grad():
        out = model(_dummy_batch(config, 2, 128))

    # log_softmax => exp(out) sums to 1 across the class dimension.
    probs = out.exp().sum(dim=2)
    assert torch.allclose(probs, torch.ones_like(probs), atol=1e-5)
    # Log-probabilities are non-positive.
    assert torch.all(out <= 1e-6)


def test_output_is_compatible_with_ctc_loss():
    config = DEFAULT_HTR_CONFIG
    model = build_model(config).eval()
    batch, width = 3, 192

    with torch.no_grad():
        log_probs = model(_dummy_batch(config, batch, width))

    t = log_probs.size(0)
    input_lengths = torch.full((batch,), t, dtype=torch.long)
    target_lengths = torch.tensor([5, 7, 3], dtype=torch.long)
    targets = torch.randint(
        1, config.num_classes_with_blank, (int(target_lengths.sum()),)
    )

    ctc = torch.nn.CTCLoss(blank=config.blank_index, zero_infinity=True)
    loss = ctc(log_probs, targets, input_lengths, target_lengths)
    assert torch.isfinite(loss)


def test_variable_width_changes_only_time_axis():
    config = DEFAULT_HTR_CONFIG
    model = build_model(config).eval()

    with torch.no_grad():
        short = model(_dummy_batch(config, 1, 128))
        long = model(_dummy_batch(config, 1, 256))

    # Wider input yields more CTC timesteps; class dim is unchanged.
    assert long.size(0) > short.size(0)
    assert short.size(2) == long.size(2) == config.num_classes_with_blank


def test_height_is_collapsed_for_configured_image_height():
    # The pooling plan must collapse the configured height to 1 for a range of
    # standard heights so the BiLSTM receives a clean width sequence.
    for height in (16, 32, 48, 64):
        config = HTRModelConfig(image_height=height)
        model = build_model(config).eval()
        with torch.no_grad():
            out = model(_dummy_batch(config, 1, 100))
        assert out.size(2) == config.num_classes_with_blank
        assert out.size(0) > 0


def test_forward_rejects_wrong_channel_count():
    config = DEFAULT_HTR_CONFIG
    model = build_model(config).eval()
    bad = torch.randn(1, config.input_channels + 1, config.image_height, 64)
    try:
        model(bad)
    except ValueError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ValueError for wrong channel count")


def test_config_round_trips_through_dict():
    config = HTRModelConfig(
        lstm_hidden_size=128,
        training=TrainingConfig(learning_rate=1e-3, batch_size=16),
    )
    restored = HTRModelConfig.from_dict(config.to_dict())
    assert restored == config
    # cnn_channels survives the round-trip as a tuple, not a list.
    assert isinstance(restored.cnn_channels, tuple)


def test_pinned_config_values_present():
    # The pinned config must capture layer sizes, LR, batch size, epochs, and
    # augmentation settings (task 6.1 acceptance).
    c = DEFAULT_HTR_CONFIG
    assert len(c.cnn_channels) > 0
    assert c.lstm_hidden_size > 0 and c.lstm_num_layers > 0
    assert math.isfinite(c.training.learning_rate) and c.training.learning_rate > 0
    assert c.training.batch_size > 0
    assert c.training.max_epochs > 0
    assert c.augmentation.enabled in (True, False)
    assert c.augmentation.rotation_degrees >= 0


# ---------------------------------------------------------------------------
# Recognition and result assembly tests (task 7.2)
# ---------------------------------------------------------------------------

import io
from PIL import Image as PILImage, ImageDraw

from app.ml.recognition import (
    RecognitionResult,
    RecognizedLine,
    RecognizedWord,
    assemble_result,
    greedy_ctc_decode,
    recognize,
    recognize_bytes,
)
from app.ml.preprocessing import preprocess
from app.ml.segmentation import segment, Region


# ---------------------------------------------------------------------------
# Synthetic helpers (reuse the same pattern as test_segmentation.py)
# ---------------------------------------------------------------------------

def _make_page_bytes(width: int = 240, height: int = 140, fmt: str = "PNG") -> bytes:
    """Two-line page: line 0 has 3 words, line 1 has 2 words."""
    img = PILImage.new("L", (width, height), color=255)
    draw = ImageDraw.Draw(img)
    # Line 0 (y ~ 20..40)
    draw.rectangle([10, 20, 39, 39], fill=0)
    draw.rectangle([80, 20, 109, 39], fill=0)
    draw.rectangle([150, 20, 179, 39], fill=0)
    # Line 1 (y ~ 90..110)
    draw.rectangle([10, 90, 49, 109], fill=0)
    draw.rectangle([120, 90, 159, 109], fill=0)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _make_blank_page_bytes(fmt: str = "PNG") -> bytes:
    img = PILImage.new("L", (200, 120), color=255)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# greedy_ctc_decode unit tests
# ---------------------------------------------------------------------------

def test_greedy_ctc_decode_blank_only_returns_empty():
    """All-blank sequence decodes to empty string."""
    T, C = 5, len(ALPHABET) + 1
    # All mass on class 0 (blank).
    log_probs = torch.full((T, C), -100.0)
    log_probs[:, 0] = 0.0  # blank wins at every step
    text, conf = greedy_ctc_decode(log_probs)
    assert text == ""
    assert conf == 0.0


def test_greedy_ctc_decode_single_char():
    """A sequence that unambiguously decodes to a single character."""
    # Class index 1 maps to ALPHABET[0].
    T, C = 4, len(ALPHABET) + 1
    log_probs = torch.full((T, C), -100.0)
    # Frames: [1, 1, 0, 0] → collapse to [1, 0] → remove blank → [1]
    log_probs[0, 1] = 0.0
    log_probs[1, 1] = 0.0
    log_probs[2, 0] = 0.0
    log_probs[3, 0] = 0.0
    text, conf = greedy_ctc_decode(log_probs)
    assert text == ALPHABET[0]
    assert 0.0 < conf <= 1.0


def test_greedy_ctc_decode_consecutive_duplicates_collapsed():
    """Consecutive identical non-blank frames collapse to one character."""
    T, C = 6, len(ALPHABET) + 1
    log_probs = torch.full((T, C), -100.0)
    # Frames: [2, 2, 2, 0, 3, 3] → collapse [2, 0, 3] → remove blank → [2, 3]
    for t in range(3):
        log_probs[t, 2] = 0.0
    log_probs[3, 0] = 0.0  # blank separator
    log_probs[4, 3] = 0.0
    log_probs[5, 3] = 0.0
    text, conf = greedy_ctc_decode(log_probs)
    assert len(text) == 2
    assert text[0] == ALPHABET[1]   # class 2 → ALPHABET[1]
    assert text[1] == ALPHABET[2]   # class 3 → ALPHABET[2]


def test_greedy_ctc_decode_same_char_with_blank_separator():
    """Two identical chars separated by blank produce two-character output."""
    T, C = 5, len(ALPHABET) + 1
    log_probs = torch.full((T, C), -100.0)
    # Frames: [1, 0, 1, 0, 0] → collapse [1, 0, 1, 0] → remove blank → [1, 1]
    log_probs[0, 1] = 0.0
    log_probs[1, 0] = 0.0  # blank separator
    log_probs[2, 1] = 0.0
    log_probs[3, 0] = 0.0
    log_probs[4, 0] = 0.0
    text, conf = greedy_ctc_decode(log_probs)
    assert text == ALPHABET[0] * 2


def test_greedy_ctc_decode_accepts_3d_single_batch():
    """Accepts (T, 1, C) shaped log-probs (direct model output shape)."""
    T, C = 4, len(ALPHABET) + 1
    log_probs = torch.full((T, 1, C), -100.0)
    log_probs[0, 0, 1] = 0.0
    log_probs[1, 0, 1] = 0.0
    log_probs[2, 0, 0] = 0.0
    log_probs[3, 0, 0] = 0.0
    text, conf = greedy_ctc_decode(log_probs)
    assert text == ALPHABET[0]


# ---------------------------------------------------------------------------
# assemble_result and recognize unit tests
# ---------------------------------------------------------------------------

def test_recognition_result_structure_from_two_line_page():
    """assemble_result returns correct line/word counts and non-empty regions."""
    img_bytes = _make_page_bytes()
    preprocessed = preprocess(img_bytes)
    seg = segment(preprocessed)
    model = build_model()

    result = assemble_result(seg, model)

    assert isinstance(result, RecognitionResult)
    assert isinstance(result.lines, list)
    assert len(result.lines) == 2
    # Line 0 has 3 words, line 1 has 2 words.
    assert len(result.lines[0].words) == 3
    assert len(result.lines[1].words) == 2


def test_every_word_carries_valid_region():
    """Every RecognizedWord has a region that lies within the source image (Req 2.4)."""
    img_bytes = _make_page_bytes()
    preprocessed = preprocess(img_bytes)
    seg = segment(preprocessed)
    model = build_model()

    result = assemble_result(seg, model)

    for line in result.lines:
        for word in line.words:
            assert isinstance(word.region, Region)
            assert word.region.is_within(seg.image_width, seg.image_height), (
                f"Word region {word.region} out of bounds "
                f"({seg.image_width}x{seg.image_height})"
            )
            assert word.region.width > 0
            assert word.region.height > 0


def test_lines_ordered_top_to_bottom_in_result():
    """Lines in RecognitionResult are ordered top-to-bottom (Req 2.1, 2.2)."""
    img_bytes = _make_page_bytes()
    preprocessed = preprocess(img_bytes)
    seg = segment(preprocessed)
    model = build_model()

    result = assemble_result(seg, model)

    line_tops = [line.region.y for line in result.lines]
    assert line_tops == sorted(line_tops)
    assert [line.index for line in result.lines] == list(range(len(result.lines)))


def test_words_ordered_left_to_right_in_result():
    """Words within each line are ordered left-to-right (Req 2.1, 2.2)."""
    img_bytes = _make_page_bytes()
    preprocessed = preprocess(img_bytes)
    seg = segment(preprocessed)
    model = build_model()

    result = assemble_result(seg, model)

    for line in result.lines:
        lefts = [w.region.x for w in line.words]
        assert lefts == sorted(lefts)
        assert [w.index for w in line.words] == list(range(len(line.words)))


def test_blank_page_yields_no_text_result():
    """A blank page returns recognized_any_text=False and no lines (Req 2.5)."""
    img_bytes = _make_blank_page_bytes()
    preprocessed = preprocess(img_bytes)
    seg = segment(preprocessed)
    model = build_model()

    result = assemble_result(seg, model)

    assert result.recognized_any_text is False
    assert result.lines == []


def test_recognize_from_preprocessed_image():
    """recognize() accepts a PreprocessedImage and returns RecognitionResult."""
    img_bytes = _make_page_bytes()
    preprocessed = preprocess(img_bytes)
    model = build_model()

    result = recognize(preprocessed, model=model)

    assert isinstance(result, RecognitionResult)
    assert isinstance(result.recognized_any_text, bool)
    assert isinstance(result.lines, list)


def test_recognize_bytes_convenience_wrapper():
    """recognize_bytes() is a round-trip from raw bytes to RecognitionResult."""
    img_bytes = _make_page_bytes()
    model = build_model()

    result = recognize_bytes(img_bytes, model=model)

    assert isinstance(result, RecognitionResult)
    assert len(result.lines) == 2


def test_recognition_result_recognized_any_text_flag():
    """recognized_any_text is False when the model decodes no non-blank chars.

    We validate that this flag is wired correctly: a blank page → False.
    A page with ink produces words (even if the untrained model decodes
    garbage), so we just verify the type contract holds.
    """
    blank_result = recognize_bytes(_make_blank_page_bytes(), model=build_model())
    assert blank_result.recognized_any_text is False

    # Page with ink — flag type must still be bool regardless of model output.
    ink_result = recognize_bytes(_make_page_bytes(), model=build_model())
    assert isinstance(ink_result.recognized_any_text, bool)


def test_recognized_word_confidence_is_in_unit_interval():
    """Confidence values for all words are in [0, 1]."""
    img_bytes = _make_page_bytes()
    model = build_model()
    result = recognize_bytes(img_bytes, model=model)

    for line in result.lines:
        for word in line.words:
            assert 0.0 <= word.confidence <= 1.0, (
                f"confidence {word.confidence!r} out of [0, 1]"
            )


def test_recognize_with_adapter_none_succeeds():
    """Passing adapter=None is a valid no-op (base model path, Req 5.5)."""
    img_bytes = _make_page_bytes()
    result = recognize_bytes(img_bytes, adapter=None, model=build_model())
    assert isinstance(result, RecognitionResult)


# ---------------------------------------------------------------------------
# Adapter composition tests (task 7.3)
# ---------------------------------------------------------------------------

import copy
import os
import tempfile

from app.ml.recognition import apply_adapter, _compose_model


def _make_adapter_weights(model: CRNN) -> bytes:
    """Serialise the classifier layer of *model* as a minimal adapter payload."""
    adapter_state = {
        "classifier.weight": model.classifier.weight.detach().clone(),
        "classifier.bias": model.classifier.bias.detach().clone(),
    }
    buf = io.BytesIO()
    torch.save(adapter_state, buf)
    return buf.getvalue()


def _make_perturbed_adapter_weights(model: CRNN) -> bytes:
    """Return adapter weights whose classifier weight is shifted by +1.0."""
    adapter_state = {
        "classifier.weight": model.classifier.weight.detach().clone() + 1.0,
        "classifier.bias": model.classifier.bias.detach().clone(),
    }
    buf = io.BytesIO()
    torch.save(adapter_state, buf)
    return buf.getvalue()


def test_apply_adapter_returns_new_model():
    """apply_adapter returns a different object from the base model."""
    base = build_model()
    adapter_bytes = _make_adapter_weights(base)
    adapted = apply_adapter(base, adapter_bytes)
    assert adapted is not base


def test_apply_adapter_does_not_mutate_base_model():
    """Base model parameters must be identical before and after apply_adapter (Req 5.2)."""
    base = build_model()
    original_weight = base.classifier.weight.detach().clone()
    original_bias = base.classifier.bias.detach().clone()

    perturbed_bytes = _make_perturbed_adapter_weights(base)
    _ = apply_adapter(base, perturbed_bytes)

    assert torch.equal(base.classifier.weight, original_weight), (
        "apply_adapter mutated the base model's classifier weight"
    )
    assert torch.equal(base.classifier.bias, original_bias), (
        "apply_adapter mutated the base model's classifier bias"
    )


def test_apply_adapter_changes_classifier_weights():
    """The adapted model has the adapter's classifier weights, not the base's."""
    base = build_model()
    perturbed_bytes = _make_perturbed_adapter_weights(base)
    adapted = apply_adapter(base, perturbed_bytes)

    expected_weight = base.classifier.weight.detach() + 1.0
    assert torch.allclose(adapted.classifier.weight, expected_weight), (
        "Adapted classifier weight does not match adapter payload"
    )


def test_apply_adapter_preserves_cnn_and_lstm_parameters():
    """CNN and LSTM parameters in the adapted model equal those of the base."""
    base = build_model()
    adapter_bytes = _make_perturbed_adapter_weights(base)
    adapted = apply_adapter(base, adapter_bytes)

    for name, param in base.cnn.named_parameters():
        adapted_param = dict(adapted.cnn.named_parameters())[name]
        assert torch.equal(param, adapted_param), (
            f"CNN parameter {name!r} changed after adapter composition"
        )

    for name, param in base.lstm.named_parameters():
        adapted_param = dict(adapted.lstm.named_parameters())[name]
        assert torch.equal(param, adapted_param), (
            f"LSTM parameter {name!r} changed after adapter composition"
        )


def test_apply_adapter_weight_only_no_bias():
    """Adapter with only classifier.weight (no bias) applies weight, keeps base bias."""
    base = build_model()
    original_bias = base.classifier.bias.detach().clone()

    # Weight-only adapter
    adapter_state = {
        "classifier.weight": base.classifier.weight.detach().clone() + 2.0,
    }
    buf = io.BytesIO()
    torch.save(adapter_state, buf)
    adapted = apply_adapter(base, buf.getvalue())

    # Bias unchanged from base.
    assert torch.equal(adapted.classifier.bias, original_bias)
    # Weight changed.
    assert torch.allclose(adapted.classifier.weight, base.classifier.weight + 2.0)


def test_compose_model_with_none_returns_same_object():
    """_compose_model with adapter=None returns the original model (Req 5.5)."""
    base = build_model()
    result = _compose_model(base, None)
    assert result is base


def test_compose_model_with_adapter_object_applies_weights():
    """_compose_model with an adapter object loads and applies the adapter weights (Req 5.4)."""
    base = build_model()
    perturbed_bytes = _make_perturbed_adapter_weights(base)

    with tempfile.NamedTemporaryFile(suffix=".pt", delete=False) as fh:
        fh.write(perturbed_bytes)
        tmp_path = fh.name

    try:
        class FakeAdapter:
            weights_path = tmp_path

        adapted = _compose_model(base, FakeAdapter())
        expected_weight = base.classifier.weight.detach() + 1.0
        assert torch.allclose(adapted.classifier.weight, expected_weight)
        # Base model is unmodified.
        assert not torch.allclose(base.classifier.weight, expected_weight)
    finally:
        os.unlink(tmp_path)


def test_compose_model_base_frozen_after_composition():
    """Base model parameters are identical after _compose_model (Req 5.2)."""
    base = build_model()
    base_params_before = {
        n: p.detach().clone() for n, p in base.named_parameters()
    }
    perturbed_bytes = _make_perturbed_adapter_weights(base)

    with tempfile.NamedTemporaryFile(suffix=".pt", delete=False) as fh:
        fh.write(perturbed_bytes)
        tmp_path = fh.name

    try:
        class FakeAdapter:
            weights_path = tmp_path

        _compose_model(base, FakeAdapter())
    finally:
        os.unlink(tmp_path)

    for name, original in base_params_before.items():
        current = dict(base.named_parameters())[name]
        assert torch.equal(current, original), (
            f"Base model parameter {name!r} was mutated by _compose_model"
        )


def test_recognize_with_adapter_produces_valid_result():
    """recognize() with a real adapter file returns a valid RecognitionResult (Req 5.4)."""
    base = build_model()
    perturbed_bytes = _make_perturbed_adapter_weights(base)

    with tempfile.NamedTemporaryFile(suffix=".pt", delete=False) as fh:
        fh.write(perturbed_bytes)
        tmp_path = fh.name

    try:
        class FakeAdapter:
            weights_path = tmp_path

        img_bytes = _make_page_bytes()
        result = recognize_bytes(img_bytes, adapter=FakeAdapter(), model=base)
        assert isinstance(result, RecognitionResult)
        assert isinstance(result.lines, list)
        assert isinstance(result.recognized_any_text, bool)
    finally:
        os.unlink(tmp_path)


def test_recognize_base_and_adapter_paths_are_independent():
    """Base-only and adapter-composed inferences each work without affecting the other."""
    base = build_model()
    perturbed_bytes = _make_perturbed_adapter_weights(base)

    with tempfile.NamedTemporaryFile(suffix=".pt", delete=False) as fh:
        fh.write(perturbed_bytes)
        tmp_path = fh.name

    try:
        class FakeAdapter:
            weights_path = tmp_path

        img_bytes = _make_page_bytes()
        result_base = recognize_bytes(img_bytes, adapter=None, model=base)
        result_adapted = recognize_bytes(img_bytes, adapter=FakeAdapter(), model=base)

        # Both return valid results — structure-level checks (Req 5.4, 5.5).
        assert isinstance(result_base, RecognitionResult)
        assert isinstance(result_adapted, RecognitionResult)
        assert len(result_base.lines) == len(result_adapted.lines)
    finally:
        os.unlink(tmp_path)
