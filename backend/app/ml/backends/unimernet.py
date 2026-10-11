"""UniMERNet-small backend adapter.

The adapter supports the official checkpoint through an isolated upstream
runtime, a standard Transformers image-to-text checkpoint, and an optional
local ``inference.py`` bridge. A config-only directory is never advertised as usable.
"""

from __future__ import annotations

import importlib.util
import io
import json
import time
from pathlib import Path
from typing import Any

from PIL import Image

from app.config.settings import get_settings
from app.ml.backends.base import EngineCandidate, EngineResult, EngineSettings


class UniMERNetEngine:
    """Load a complete local UniMERNet snapshot when its inference path works."""

    engine_id = "unimernet-small"
    engine_version = "snapshot"

    def __init__(self, model_dir: Path | None = None) -> None:
        default_dir = (
            Path(get_settings().object_store_path).expanduser().resolve()
            / "models"
            / "unimernet_small"
        )
        self.model_dir = Path(model_dir) if model_dir else default_dir
        self._model: Any = None
        self._processor: Any = None
        self._bridge: Any = None
        self._bridge_probe: Any = None
        self._bridge_with_settings: Any = None
        self._availability: bool | None = None
        self._reason = (
            f"UniMERNet-small weights were not found at {self.model_dir}. "
            "Download the official snapshot into that directory before enabling formula recognition."
        )
        self._provenance = self._read_provenance()

    def _read_provenance(self) -> dict[str, Any]:
        for name in ("model_artifact.json", "artifact_manifest.json"):
            path = self.model_dir / name
            if path.is_file():
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                    return dict(value) if isinstance(value, dict) else {}
                except (OSError, json.JSONDecodeError):
                    return {}
        return {}

    def is_available(self) -> bool:
        if self._availability is not None:
            return self._availability
        if not self.model_dir.is_dir():
            self._availability = False
            return False
        try:
            self._ensure_loaded()
            if self._bridge_probe is not None and not self._bridge_probe():
                raise RuntimeError("upstream bridge did not pass its inference probe")
            self._availability = True
        except Exception as exc:
            self._reason = f"UniMERNet snapshot is unavailable: {exc}"
            self._availability = False
        return bool(self._availability)

    def recognize_crop(self, image_bytes: bytes, settings: EngineSettings | None = None) -> EngineResult:
        if not self.is_available():
            return EngineResult(engine_id=self.engine_id, engine_version=self.engine_version, available=False, reason=self._reason, provenance=self._provenance)
        started = time.perf_counter()
        inference_settings = settings or EngineSettings()
        try:
            truncated = False
            with Image.open(io.BytesIO(image_bytes)) as opened:
                image = opened.convert("RGB")
            if self._bridge is not None:
                prediction = (
                    self._bridge_with_settings(image_bytes, max_tokens=inference_settings.max_tokens, beam_size=inference_settings.beam_size)
                    if self._bridge_with_settings is not None
                    else self._bridge(image_bytes)
                )
                if isinstance(prediction, dict):
                    text = str(prediction["text"])
                    truncated = bool(prediction.get("truncated", False))
                else:
                    text = str(prediction)
            else:
                inputs = self._processor(images=image, return_tensors="pt")
                generated = self._model.generate(
                    **inputs,
                    max_new_tokens=inference_settings.max_tokens,
                    num_beams=max(1, min(inference_settings.beam_size, 8)),
                )
                tokenizer = getattr(self._processor, "tokenizer", self._processor)
                decoded = tokenizer.batch_decode(generated, skip_special_tokens=True)
                text = decoded[0].strip() if decoded else ""
        except Exception as exc:
            return EngineResult(engine_id=self.engine_id, engine_version=self.engine_version, available=False, reason=f"UniMERNet inference failed: {exc}", provenance=self._provenance)
        return EngineResult(
            engine_id=self.engine_id,
            engine_version=str(self._provenance.get("revision") or self.engine_version),
            candidates=[EngineCandidate(text=text, kind="latex", truncated=truncated)],
            latency_ms=(time.perf_counter() - started) * 1000,
            provenance=self._provenance,
            settings={"max_tokens": inference_settings.max_tokens, "beam_size": inference_settings.beam_size},
        )

    def _ensure_loaded(self) -> None:
        if self._bridge is not None or self._model is not None:
            return
        bridge_path = self.model_dir / "inference.py"
        if bridge_path.is_file():
            spec = importlib.util.spec_from_file_location("hnd_unimernet_bridge", bridge_path)
            if spec is None or spec.loader is None:
                raise RuntimeError("could not load upstream inference bridge")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            infer = getattr(module, "infer", None)
            if not callable(infer):
                raise RuntimeError("inference.py must export infer(image_bytes)")
            self._bridge = infer
            self._bridge_probe = getattr(module, "is_available", None)
            self._bridge_with_settings = getattr(module, "infer_with_settings", None)
            self._ensure_provenance()
            return
        if (self.model_dir / "unimernet_small.pth").is_file():
            from app.ml.backends.unimernet_runtime import get_runtime

            runtime = get_runtime(self.model_dir)
            self._bridge = runtime.infer
            self._bridge_probe = runtime.probe
            self._bridge_with_settings = runtime.infer
            self._ensure_provenance()
            return
        from transformers import AutoProcessor, VisionEncoderDecoderModel

        self._processor = AutoProcessor.from_pretrained(str(self.model_dir), local_files_only=True)
        self._model = VisionEncoderDecoderModel.from_pretrained(str(self.model_dir), local_files_only=True)
        self._model.eval()
        self._ensure_provenance()

    def _ensure_provenance(self) -> None:
        from app.ml.artifacts import sha256_tree

        if not self._provenance:
            self._provenance = {
                "model_id": "wanderkid/unimernet_small",
                "weight_sha256": sha256_tree(self.model_dir),
            }
        else:
            if "weight_sha256" not in self._provenance:
                self._provenance["weight_sha256"] = sha256_tree(self.model_dir)


__all__ = ["UniMERNetEngine"]
