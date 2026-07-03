"""Project diagnostics for local, Docker, and HTR model wiring.

Run from the repository root:

    backend/.venv/bin/python scripts/doctor.py

The script is intentionally stdlib-first so it still provides useful Docker and
configuration checks even when the ML dependencies are not importable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
HOST_MODEL_DIR = BACKEND / "var" / "objectstore" / "models" / "base_model"
HOST_WEIGHTS = HOST_MODEL_DIR / "base_model.pt"
HOST_META = HOST_MODEL_DIR / "base_model.meta.json"


def ok(message: str) -> None:
    print(f"[ok]   {message}")


def warn(message: str) -> None:
    print(f"[warn] {message}")


def fail(message: str) -> None:
    print(f"[fail] {message}")


def run(cmd: list[str], *, timeout: int = 12) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return 127, f"{cmd[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, f"{' '.join(cmd)} timed out"
    return completed.returncode, completed.stdout.strip()


def fetch_json(url: str, *, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            parsed = json.loads(body)
            return True, json.dumps(parsed, sort_keys=True)
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, str(exc)


def check_host_model() -> None:
    print("\nHost model artifact")
    if HOST_WEIGHTS.is_file():
        size_mb = HOST_WEIGHTS.stat().st_size / (1024 * 1024)
        ok(f"{HOST_WEIGHTS.relative_to(ROOT)} exists ({size_mb:.1f} MiB)")
    else:
        fail(f"missing {HOST_WEIGHTS.relative_to(ROOT)}")

    if HOST_META.is_file():
        try:
            meta = json.loads(HOST_META.read_text(encoding="utf-8"))
            ok(
                "metadata: "
                f"dataset={meta.get('dataset_version')}, "
                f"epochs={meta.get('epochs_run')}, "
                f"loss={meta.get('final_train_loss')}"
            )
        except Exception as exc:
            warn(f"could not parse model metadata: {exc}")
    else:
        fail(f"missing {HOST_META.relative_to(ROOT)}")


def check_env() -> None:
    print("\nEnvironment files")
    root_env = ROOT / ".env"
    backend_env = BACKEND / ".env"
    if root_env.exists():
        ok(".env exists for docker compose")
        text = root_env.read_text(encoding="utf-8", errors="replace")
        if "HND_OBJECT_STORE_PATH=/app/var/objectstore" in text:
            warn(
                "root .env uses the container object-store path. That is correct "
                "for docker compose, but run local backend commands from backend/ "
                "or unset HND_OBJECT_STORE_PATH."
            )
    else:
        warn(".env is missing; docker compose will use defaults")

    if backend_env.exists():
        warn("backend/.env exists and will override local backend settings")
    else:
        ok("backend/.env absent; local backend uses ./var/objectstore")


def check_local_health() -> None:
    print("\nHTTP health probes")
    probes = [
        ("backend", "http://127.0.0.1:8000/health"),
        ("vite proxy", "http://127.0.0.1:5173/api/health"),
        ("docker/nginx", "http://127.0.0.1/api/health"),
    ]
    for label, url in probes:
        success, detail = fetch_json(url)
        if success:
            ok(f"{label} {url} -> {detail}")
        else:
            warn(f"{label} {url} unavailable: {detail}")


def check_python_model_smoke() -> None:
    print("\nPython model smoke")
    sys.path.insert(0, str(BACKEND))
    previous_cwd = Path.cwd()
    try:
        os.chdir(BACKEND)
        from app.ml.recognition import get_base_model

        model = get_base_model(force_reload=True)
        ok(
            "loaded base model through app.ml.recognition "
            f"(height={model.config.image_height}, classes={model.config.num_classes})"
        )
    except Exception as exc:
        fail(f"could not load base model through backend code: {exc}")
    finally:
        os.chdir(previous_cwd)


def check_docker() -> None:
    print("\nDocker compose")
    code, output = run(["docker", "compose", "ps"])
    if code != 0:
        warn(f"docker compose ps failed: {output}")
        return
    if "backend" not in output and "frontend" not in output:
        warn("compose stack is not running")
        return
    ok("compose stack is running")
    print(output)

    code, output = run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "backend",
            "python",
            "-c",
            (
                "from pathlib import Path; "
                "p=Path('/app/var/objectstore/models/base_model/base_model.pt'); "
                "print(p.exists(), p.stat().st_size if p.exists() else 0)"
            ),
        ]
    )
    if code == 0 and output.startswith("True"):
        ok(f"backend container sees model artifact: {output}")
    else:
        fail(f"backend container cannot see model artifact: {output}")


def main() -> int:
    print(f"Handwriting Notes Digitizer doctor\nroot: {ROOT}")
    check_host_model()
    check_env()
    check_local_health()
    check_python_model_smoke()
    check_docker()
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
