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
HOST_MODEL_ROOT = BACKEND / "var" / "objectstore" / "models"
HOST_MODEL_DIR = HOST_MODEL_ROOT / "base_model"
HOST_WEIGHTS = HOST_MODEL_DIR / "base_model.pt"
HOST_META = HOST_MODEL_DIR / "base_model.meta.json"
HOST_UNIMERNET_DIR = HOST_MODEL_ROOT / "unimernet_small"
HOST_COMPACT_CHECKPOINT = HOST_MODEL_ROOT / "compact_math" / "checkpoint.pt"
failures = 0


def ok(message: str) -> None:
    print(f"[ok]   {message}")


def warn(message: str) -> None:
    print(f"[warn] {message}")


def fail(message: str) -> None:
    global failures
    failures += 1
    print(f"[fail] {message}")


def run(
    cmd: list[str], *, timeout: int = 12, cwd: Path = ROOT
) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            cmd,
            cwd=cwd,
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
    print("\nHost model artifacts")
    found = False
    if HOST_WEIGHTS.is_file():
        found = True
        size_mb = HOST_WEIGHTS.stat().st_size / (1024 * 1024)
        ok(f"{HOST_WEIGHTS.relative_to(ROOT)} exists ({size_mb:.1f} MiB)")
    else:
        warn(f"legacy prose artifact not installed: {HOST_WEIGHTS.relative_to(ROOT)}")

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
        warn(f"legacy prose metadata not installed: {HOST_META.relative_to(ROOT)}")

    if HOST_UNIMERNET_DIR.is_dir() and (
        (HOST_UNIMERNET_DIR / "model_artifact.json").is_file()
        or (HOST_UNIMERNET_DIR / "config.json").is_file()
    ):
        found = True
        ok(f"UniMERNet snapshot directory exists: {HOST_UNIMERNET_DIR.relative_to(ROOT)}")
    else:
        warn(f"UniMERNet-small snapshot not installed: {HOST_UNIMERNET_DIR.relative_to(ROOT)}")

    if HOST_COMPACT_CHECKPOINT.is_file():
        found = True
        ok(f"compact math checkpoint exists: {HOST_COMPACT_CHECKPOINT.relative_to(ROOT)}")
    else:
        warn(f"compact math checkpoint not installed: {HOST_COMPACT_CHECKPOINT.relative_to(ROOT)}")

    if not found:
        warn("No loadable recognition artifact is installed; /health will report recognition as unavailable.")


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
                "for docker compose; backend settings intentionally ignore this "
                "file for local commands."
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
        from app.ml.recognition import get_loaded_model_status
        from app.services.math_regions_api import get_formula_engine_status

        prose = get_loaded_model_status(force_reload=True)
        formula = get_formula_engine_status()
        if prose.loaded and prose.probe_ok:
            ok(f"loaded prose model through app.ml.recognition ({prose.artifact_path})")
        else:
            warn(f"prose recognition unavailable: {prose.reason or 'artifact not installed'}")
        if formula.get("loaded"):
            ok(f"loaded formula backend: {formula.get('backend')}")
        else:
            warn(f"formula recognition unavailable: {formula.get('reason') or 'artifact not installed'}")
    except Exception as exc:
        fail(f"could not load base model through backend code: {exc}")
    finally:
        os.chdir(previous_cwd)


def check_migrations() -> None:
    print("\nDatabase migrations")
    compose_code, compose_backend = run(["docker", "compose", "ps", "-q", "backend"])
    if compose_code == 0 and compose_backend:
        command = [
            "docker",
            "compose",
            "exec",
            "-T",
            "backend",
            "python",
            "-m",
            "alembic",
            "check",
        ]
        location = "Compose database"
        cwd = ROOT
    elif (BACKEND / "hnd.db").exists():
        command = [str(BACKEND / ".venv" / "bin" / "python"), "-m", "alembic", "check"]
        location = "local database"
        cwd = BACKEND
    else:
        warn("no running Compose backend or local backend/hnd.db; skipping migration check")
        return

    code, output = run(command, cwd=cwd)
    if code == 0:
        ok(f"Alembic schema matches the ORM models ({location})")
    else:
        fail(f"Alembic schema check failed ({location}): {output}")


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
                "root=Path('/app/var/objectstore/models'); "
                "paths=[root/'base_model'/'base_model.pt', root/'unimernet_small'/'model_artifact.json', root/'compact_math'/'checkpoint.pt']; "
                "print([(str(p), p.exists(), p.stat().st_size if p.exists() else 0) for p in paths])"
            ),
        ]
    )
    if code == 0 and output and "True" in output:
        ok(f"backend container sees model artifact: {output}")
    else:
        warn(f"backend container has no recognition artifact mounted: {output}")


def main() -> int:
    print(f"Handwriting Notes Digitizer doctor\nroot: {ROOT}")
    check_host_model()
    check_env()
    check_migrations()
    check_local_health()
    check_python_model_smoke()
    check_docker()
    print("\nDone.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
