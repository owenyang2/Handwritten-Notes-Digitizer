"""Optional upstream runtime failures must not become successful predictions."""

from __future__ import annotations

import io
import json
from pathlib import Path
import sys

from PIL import Image
import pytest

from app.ml.backends.base import EngineSettings
from app.ml.backends.unimernet import UniMERNetEngine
from app.ml.backends.unimernet_runtime import UniMERNetRuntime, get_runtime


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(output, format="PNG")
    return output.getvalue()


def make_bridge(tmp_path: Path, body: str) -> UniMERNetEngine:
    (tmp_path / "inference.py").write_text(body)
    (tmp_path / "model_artifact.json").write_text(json.dumps({"revision": "test", "weight_sha256": "fixture"}))
    return UniMERNetEngine(model_dir=tmp_path)


def test_failed_bridge_probe_reports_unavailable(tmp_path):
    engine = make_bridge(tmp_path, "def infer(data): return 'fake'\ndef is_available(): return False\n")
    result = engine.recognize_crop(png_bytes())
    assert not result.available
    assert result.candidates == []
    assert "probe" in result.reason


def test_bridge_preserves_generation_settings_and_truncation(tmp_path):
    engine = make_bridge(tmp_path, """
def infer(data):
    raise AssertionError('bounded inference should be used')
def is_available():
    return True
def infer_with_settings(data, *, max_tokens, beam_size):
    assert max_tokens == 7 and beam_size == 2
    return {'text': 'x^2', 'truncated': True}
""")
    result = engine.recognize_crop(png_bytes(), EngineSettings(max_tokens=7, beam_size=2))
    assert result.available and result.text == "x^2" and result.truncated


def fake_runtime(tmp_path: Path, script: str) -> UniMERNetRuntime:
    worker = tmp_path / "worker.py"
    worker.write_text(script)
    runtime = UniMERNetRuntime(tmp_path)
    runtime.python = Path(sys.executable)
    runtime.worker = worker
    return runtime


def test_runtime_reuses_one_probed_process_and_closes_it(tmp_path):
    runtime = fake_runtime(tmp_path, """
import json, sys
print(json.dumps({'ready': True}), flush=True)
for line in sys.stdin:
    payload = json.loads(line)
    assert payload['max_tokens'] == 5
    print(json.dumps({'text': 'x', 'truncated': False}), flush=True)
""")
    try:
        assert runtime.probe()
        process = runtime._process
        assert runtime.infer(png_bytes(), max_tokens=5)["text"] == "x"
        assert runtime.infer(png_bytes(), max_tokens=5)["text"] == "x"
        assert runtime._process is process
    finally:
        runtime.close()
    assert process.poll() is not None


def test_runtime_startup_failure_never_passes_probe(tmp_path):
    runtime = fake_runtime(tmp_path, "import json\nprint(json.dumps({'error': 'incompatible weights'}), flush=True)\n")
    with pytest.raises(RuntimeError, match="incompatible weights"):
        runtime.probe()
    assert runtime._process is None


def test_timeout_terminates_worker_and_next_request_can_restart(tmp_path, monkeypatch):
    runtime = fake_runtime(tmp_path, """
import json, sys, time
print(json.dumps({'ready': True}), flush=True)
for line in sys.stdin:
    time.sleep(2)
""")
    runtime.probe()
    original = runtime._process
    from app.config.settings import get_settings
    monkeypatch.setattr(get_settings(), "recognition_timeout_seconds", 0.03)
    try:
        with pytest.raises(TimeoutError):
            runtime.infer(png_bytes())
        assert original.poll() is not None
        assert runtime._process is None
        assert runtime.probe()
        assert runtime._process is not original
    finally:
        runtime.close()


def test_runtime_cache_is_scoped_to_model_directory(tmp_path):
    assert get_runtime(tmp_path) is get_runtime(tmp_path / ".")
    assert get_runtime(tmp_path) is not get_runtime(tmp_path / "other")
