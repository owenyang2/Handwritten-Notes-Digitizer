"""CNN + BiLSTM + CTC handwriting recognition network (the "CRNN").

This is the PyTorch architecture for the shared ``Base_Model`` of the
HTR_Engine. It maps a fixed-height, variable-width grayscale line image to a
sequence of per-timestep log-probability distributions over the alphabet plus
the CTC blank symbol — the form consumed by ``torch.nn.CTCLoss`` for training
and by a CTC decoder at inference (Requirement 11.2).

Pipeline
--------
1. **CNN feature extractor** — stacked convolutions with pooling collapse the
   image height to 1 while preserving (and slightly downsampling) the width,
   producing a width-indexed sequence of feature vectors.
2. **BiLSTM** — a bidirectional LSTM models left/right context across the
   width sequence.
3. **CTC head** — a linear layer projects each timestep to
   ``num_classes + 1`` logits; ``log_softmax`` yields CTC log-probabilities.

Output shape is ``(T, N, C)`` (time, batch, classes) to match the
``CTCLoss`` convention where the input is expected as ``(T, N, C)`` log-probs.

Architecture and hyper-parameters come from :mod:`app.ml.htr_config` so the
model is fully described by a pinned, serialisable configuration.
"""

from __future__ import annotations

import torch
from torch import Tensor, nn

from app.ml.htr_config import DEFAULT_HTR_CONFIG, HTRModelConfig


class CNNFeatureExtractor(nn.Module):
    """Convolutional stack that turns a line image into a width-wise sequence.

    Given an input of shape ``(N, C_in, H, W)`` it returns features of shape
    ``(N, C_out, 1, W')`` where the height has been collapsed to 1 and the width
    has been downsampled by the pooling schedule. ``C_out`` is the last value in
    ``config.cnn_channels``.
    """

    def __init__(self, config: HTRModelConfig) -> None:
        super().__init__()
        self.config = config

        channels = config.cnn_channels
        # Pooling schedule: the first two stages halve both dimensions; later
        # stages pool only the height with a (2,1) kernel so the width (the CTC
        # time axis) is preserved for longer. This is the standard CRNN recipe.
        # We derive a schedule that always collapses the height to 1 for the
        # configured image height while keeping a useful width sequence.
        layers: list[nn.Module] = []
        in_ch = config.input_channels
        # Decide per-stage pooling so that the product of height-pool factors
        # reduces image_height to 1.
        pool_plan = self._build_pool_plan(len(channels), config.image_height)

        for stage, out_ch in enumerate(channels):
            layers.append(
                nn.Conv2d(in_ch, out_ch, kernel_size=3, stride=1, padding=1)
            )
            layers.append(nn.BatchNorm2d(out_ch))
            layers.append(nn.ReLU(inplace=True))
            pool = pool_plan[stage]
            if pool is not None:
                layers.append(nn.MaxPool2d(kernel_size=pool, stride=pool))
            in_ch = out_ch

        self.features = nn.Sequential(*layers)
        self.out_channels = channels[-1]

    @staticmethod
    def _build_pool_plan(
        num_stages: int, image_height: int
    ) -> list[tuple[int, int] | None]:
        """Construct a per-stage pooling plan collapsing height to 1.

        A 2x2 max-pool with stride 2 maps height ``h`` to ``(h - 2) // 2 + 1``.
        We apply height pooling on successive early stages until the simulated
        height reaches 1, so the plan is exact for any input height (not just
        powers of two). The first two height-pools also halve the width
        (standard CRNN); subsequent height-pools use a ``(2, 1)`` kernel that
        leaves the width — the CTC time axis — untouched. Stages beyond the
        height-reduction budget apply no pooling.
        """
        plan: list[tuple[int, int] | None] = []
        h = image_height
        width_pools_done = 0
        for _ in range(num_stages):
            if h > 1:
                if width_pools_done < 2:
                    plan.append((2, 2))  # halve height and width
                    width_pools_done += 1
                else:
                    plan.append((2, 1))  # halve height only, keep width
                # Simulate the max-pool height reduction (kernel 2, stride 2).
                h = (h - 2) // 2 + 1
            else:
                plan.append(None)
        return plan

    def forward(self, x: Tensor) -> Tensor:
        return self.features(x)


class CRNN(nn.Module):
    """CNN + BiLSTM + CTC network for handwriting line recognition.

    Parameters
    ----------
    config:
        Architecture configuration. Defaults to the pinned
        :data:`app.ml.htr_config.DEFAULT_HTR_CONFIG`.

    Forward input
    -------------
    ``images``: float tensor of shape ``(N, C_in, H, W)`` with ``C_in`` and
    ``H`` matching the configuration (grayscale, ``image_height``). The width
    ``W`` is variable.

    Forward output
    --------------
    Log-probabilities of shape ``(T, N, num_classes_with_blank)`` where ``T`` is
    the downsampled width (CTC time steps). This matches the input layout
    expected by :class:`torch.nn.CTCLoss`.
    """

    def __init__(self, config: HTRModelConfig | None = None) -> None:
        super().__init__()
        self.config = config or DEFAULT_HTR_CONFIG

        self.cnn = CNNFeatureExtractor(self.config)

        lstm_input_size = self.cnn.out_channels
        self.lstm = nn.LSTM(
            input_size=lstm_input_size,
            hidden_size=self.config.lstm_hidden_size,
            num_layers=self.config.lstm_num_layers,
            batch_first=False,  # we use (T, N, C) ordering for CTC
            bidirectional=self.config.bidirectional,
            dropout=(
                self.config.lstm_dropout
                if self.config.lstm_num_layers > 1
                else 0.0
            ),
        )

        lstm_output_size = self.config.lstm_hidden_size * (
            2 if self.config.bidirectional else 1
        )
        self.classifier = nn.Linear(
            lstm_output_size, self.config.num_classes_with_blank
        )

    def forward(self, images: Tensor) -> Tensor:
        if images.dim() != 4:
            raise ValueError(
                "expected input of shape (N, C, H, W), got "
                f"{tuple(images.shape)}"
            )
        expected_c = self.config.input_channels
        if images.size(1) != expected_c:
            raise ValueError(
                f"expected {expected_c} input channel(s), got {images.size(1)}"
            )

        # CNN -> (N, C_out, H', W')
        feats = self.cnn(images)
        n, c, h, w = feats.shape
        if h != 1:
            raise ValueError(
                "CNN feature extractor did not collapse height to 1 "
                f"(got height {h}); check image_height vs cnn pooling plan"
            )

        # (N, C, 1, W) -> (W, N, C): width becomes the time axis.
        feats = feats.squeeze(2)  # (N, C, W)
        feats = feats.permute(2, 0, 1).contiguous()  # (W=T, N, C)

        # BiLSTM -> (T, N, lstm_output_size)
        seq, _ = self.lstm(feats)

        # CTC head -> (T, N, num_classes_with_blank)
        logits = self.classifier(seq)
        log_probs = nn.functional.log_softmax(logits, dim=2)
        return log_probs

    def output_length(self, input_width: int) -> int:
        """Return the number of CTC timesteps ``T`` for a given input width.

        Useful for building the ``input_lengths`` argument to ``CTCLoss``. The
        width is halved once for each of the first two pooling stages.
        """
        # Run a tiny dummy tensor through the CNN to read off the width; this is
        # exact regardless of the pooling plan and avoids duplicating the math.
        with torch.no_grad():
            dummy = torch.zeros(
                1,
                self.config.input_channels,
                self.config.image_height,
                max(input_width, 1),
            )
            feats = self.cnn(dummy)
        return int(feats.size(3))


def build_model(config: HTRModelConfig | None = None) -> CRNN:
    """Factory that constructs a :class:`CRNN` from a (pinned) configuration."""
    return CRNN(config or DEFAULT_HTR_CONFIG)


__all__ = ["CNNFeatureExtractor", "CRNN", "build_model"]
