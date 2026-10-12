"""Compact native PyTorch formula engine.

Untrained random weights are never used for application recognition. The
engine is available only when a trained checkpoint is present or a model
instance is injected by training/tests.
"""

from __future__ import annotations

import io
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from app.config.settings import get_settings
from app.ml.backends.base import EngineCandidate, EngineResult, EngineSettings
from app.ml.math.model import CompactMathConfig, CompactMathModel
from app.ml.math.tokenizer import LatexTokenizer


def default_compact_checkpoint() -> Path:
    return (
        Path(get_settings().object_store_path).expanduser().resolve()
        / "models"
        / "compact_math"
        / "checkpoint.pt"
    )


class CompactMathEngine:
    engine_id = "compact-math"
    engine_version = "v0"

    def __init__(
        self,
        model: CompactMathModel | None = None,
        checkpoint: Path | None = None,
    ) -> None:
        self.checkpoint = Path(checkpoint) if checkpoint else default_compact_checkpoint()
        self.model = model
        self._availability: bool | None = True if model is not None else None
        self._metadata: dict = {}
        self._reason = (
            f"Compact math checkpoint was not found at {self.checkpoint}. "
            "Train `python -m training.cli` or download UniMERNet-small for formula recognition."
        )

    def is_available(self) -> bool:
        if self._availability is not None:
            return self._availability
        if not self.checkpoint.is_file():
            self._availability = False
            return False
        try:
            # Loading here ensures a corrupt/incompatible artifact is not
            # advertised as available merely because a file exists.
            self._ensure_model()
        except Exception as exc:
            self._reason = f"Compact math checkpoint is unavailable: {exc}"
            self._availability = False
        else:
            self._availability = True
        return bool(self._availability)

    def recognize_crop(self, image_bytes: bytes, settings: EngineSettings | None = None) -> EngineResult:
        if not self.is_available():
            return EngineResult(
                engine_id=self.engine_id,
                engine_version=self.engine_version,
                available=False,
                reason=self._reason,
            )
        started = time.perf_counter()
        try:
            model = self._ensure_model()
            with Image.open(io.BytesIO(image_bytes)) as opened:
                image = opened.convert("L")
            array = np.asarray(image, dtype="float32") / 255.0
            tensor = torch.from_numpy(array).unsqueeze(0).unsqueeze(0)
            target_h = model.config.image_height
            scale = target_h / max(array.shape[0], 1)
            new_w = max(1, min(model.config.max_width, int(round(array.shape[1] * scale))))
            resized = torch.nn.functional.interpolate(
                tensor, size=(target_h, new_w), mode="bilinear", align_corners=False
            )
            inference_settings = settings or EngineSettings(
                max_tokens=model.config.max_tokens,
                beam_size=model.config.beam_size,
            )
            if inference_settings.beam_size > 1:
                texts, truncated, scores = model.beam_decode(
                    resized,
                    max_tokens=inference_settings.max_tokens,
                    beam_size=inference_settings.beam_size,
                )
            else:
                texts, truncated = model.greedy_decode(
                    resized, max_tokens=inference_settings.max_tokens
                )
                scores = [None] * len(texts)
        except Exception as exc:
            return EngineResult(
                engine_id=self.engine_id,
                engine_version=self.engine_version,
                available=False,
                reason=f"Compact math inference failed: {exc}",
            )
        latency_ms = (time.perf_counter() - started) * 1000
        return EngineResult(
            engine_id=self.engine_id,
            engine_version=self.engine_version,
            candidates=[
                EngineCandidate(
                    text=texts[0] if texts else "",
                    score=float(scores[0]) if scores and scores[0] is not None else None,
                    truncated=bool(truncated[0]) if truncated else True,
                    kind="latex",
                )
            ],
            latency_ms=latency_ms,
            provenance={
                "backend": self.engine_id,
                "checkpoint": str(self.checkpoint),
                "weight_sha256": self._metadata.get("weight_sha256"),
                "model_id": self._metadata.get("model_id"),
            },
            settings={
                "max_tokens": inference_settings.max_tokens,
                "beam_size": inference_settings.beam_size,
            },
        )

    def _ensure_model(self) -> CompactMathModel:
        if self.model is not None:
            self.model.eval()
            return self.model
        payload = torch.load(self.checkpoint, map_location="cpu", weights_only=False)
        if not isinstance(payload, dict):
            raise RuntimeError("Compact math checkpoint must be a mapping.")
        config_data = payload.get("config") or {}
        allowed = CompactMathConfig.__dataclass_fields__
        config = CompactMathConfig(
            **{key: value for key, value in config_data.items() if key in allowed}
        )
        token_to_id = payload.get("tokenizer")
        tokenizer = (
            LatexTokenizer(
                token_to_id=token_to_id,
                id_to_token={int(i): tok for tok, i in token_to_id.items()},
            )
            if isinstance(token_to_id, dict)
            else LatexTokenizer.default()
        )
        model = CompactMathModel(tokenizer=tokenizer, config=config)
        state = payload.get("model") or payload.get("state_dict")
        if not isinstance(state, dict):
            raise RuntimeError("Compact math checkpoint is missing model weights.")
        model.load_state_dict(state)
        model.eval()
        self.model = model
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            self._metadata = dict(metadata)
        else:
            self._metadata = {}
        # A runtime provenance record must still identify an artifact when a
        # legacy checkpoint predates the metadata section.
        self._metadata.setdefault(
            "weight_sha256",
            hashlib.sha256(self.checkpoint.read_bytes()).hexdigest(),
        )
        self._metadata.setdefault("model_id", "compact-math-native-pytorch")
        self._availability = True
        return model
