"""Optional pretrained line recognizer; legacy CRNN adapters never apply to it."""
from dataclasses import replace
from pathlib import Path
import threading

from app.config.settings import get_settings
from app.ml.backends.unimernet import UniMERNetEngine
from app.ml.backends.unimernet_runtime import UniMERNetRuntime

_runtimes = {}
_lock = threading.Lock()


class TrOCREngine(UniMERNetEngine):
    engine_id = "trocr-handwritten"

    def __init__(self, model_dir=None):
        super().__init__(model_dir or Path(get_settings().object_store_path).expanduser().resolve() / "models" / "trocr_handwritten")
        self._reason = "The pretrained prose checkpoint is unavailable"
        if not self._provenance:
            self._provenance = {"model_id": "microsoft/trocr-base-handwritten"}

    def _ensure_loaded(self):
        if self._bridge is not None:
            return
        if not (self.model_dir / "model.safetensors").is_file():
            raise ValueError("Missing TrOCR safetensors checkpoint")
        with _lock:
            key = self.model_dir.resolve()
            if key not in _runtimes:
                runtime = UniMERNetRuntime(key)
                runtime.worker = Path(__file__).resolve().parents[4] / "scripts" / "run_trocr.py"
                _runtimes[key] = runtime
            runtime = _runtimes[key]
        self._bridge = self._bridge_with_settings = runtime.infer
        self._bridge_probe = runtime.probe
        self._ensure_provenance()

    def recognize_crop(self, image_bytes, settings=None):
        result = super().recognize_crop(image_bytes, settings)
        return replace(result, candidates=[replace(candidate, kind="text") for candidate in result.candidates])


import atexit


@atexit.register
def close_runtimes():
    for runtime in _runtimes.values():
        runtime.close()
