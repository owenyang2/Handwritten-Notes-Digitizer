"""Keep the optional upstream UniMERNet runtime isolated and warm."""

from __future__ import annotations

import atexit
import base64
import json
import os
from pathlib import Path
import select
import subprocess
import tempfile
import threading


class UniMERNetRuntime:
    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir.resolve()
        self.python = self.model_dir.parent / "unimernet_runtime" / ".venv" / "bin" / "python"
        self.worker = Path(__file__).resolve().parents[4] / "scripts" / "run_unimernet.py"
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._errors = None

    def _read(self, timeout: float) -> dict:
        process = self._process
        if process is None or process.stdout is None:
            raise RuntimeError("UniMERNet runtime is not running")
        ready, _, _ = select.select([process.stdout], [], [], timeout)
        if not ready:
            self.close()
            raise TimeoutError("UniMERNet runtime exceeded its time budget")
        line = process.stdout.readline()
        if not line:
            detail = ""
            if self._errors is not None:
                self._errors.seek(0)
                detail = self._errors.read()[-2000:]
            self.close()
            raise RuntimeError(f"UniMERNet runtime stopped: {detail}")
        payload = json.loads(line)
        if payload.get("error"):
            raise RuntimeError(str(payload["error"]))
        return payload

    def probe(self) -> bool:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return True
            self.close()
            if not self.python.is_file():
                raise RuntimeError("Install the isolated UniMERNet environment; see docs/guides/recognition-and-ipad.md")
            self._errors = tempfile.TemporaryFile(mode="w+t")
            self._process = subprocess.Popen(
                [str(self.python), "-u", str(self.worker), "--model-dir", str(self.model_dir)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._errors,
                text=True,
                env={
                    **os.environ,
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "TOKENIZERS_PARALLELISM": "false",
                    "HF_HOME": str(self.model_dir.parent / "unimernet_runtime" / "cache" / "huggingface"),
                    "MPLCONFIGDIR": str(self.model_dir.parent / "unimernet_runtime" / "cache" / "matplotlib"),
                },
            )
            try:
                if self._read(60).get("ready") is not True:
                    raise RuntimeError("UniMERNet runtime did not pass its inference probe")
            except Exception:
                self.close()
                raise
            return True

    def infer(self, image_bytes: bytes, *, max_tokens: int = 160, beam_size: int = 1) -> dict:
        from app.config.settings import get_settings

        with self._lock:
            self.probe()
            assert self._process is not None and self._process.stdin is not None
            request = {"image": base64.b64encode(image_bytes).decode("ascii"), "max_tokens": max_tokens, "beam_size": beam_size}
            self._process.stdin.write(json.dumps(request) + "\n")
            self._process.stdin.flush()
            return self._read(float(get_settings().recognition_timeout_seconds))

    def close(self) -> None:
        with self._lock:
            if self._process is not None:
                if self._process.poll() is None:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait(timeout=3)
                for stream in (self._process.stdin, self._process.stdout):
                    if stream is not None:
                        stream.close()
                self._process = None
            if self._errors is not None:
                self._errors.close()
                self._errors = None


_runtimes: dict[Path, UniMERNetRuntime] = {}
_runtime_lock = threading.Lock()


def get_runtime(model_dir: Path) -> UniMERNetRuntime:
    with _runtime_lock:
        key = model_dir.resolve()
        if key not in _runtimes:
            _runtimes[key] = UniMERNetRuntime(key)
        return _runtimes[key]


@atexit.register
def close_runtimes() -> None:
    for runtime in _runtimes.values():
        runtime.close()
