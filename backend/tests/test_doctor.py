"""Regression tests for the repository diagnostic exit status."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_doctor() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "doctor.py"
    spec = importlib.util.spec_from_file_location("doctor_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _disable_checks(monkeypatch, doctor: ModuleType) -> None:
    for name in (
        "check_host_model",
        "check_env",
        "check_migrations",
        "check_local_health",
        "check_python_model_smoke",
        "check_docker",
    ):
        monkeypatch.setattr(doctor, name, lambda: None)


def test_doctor_returns_zero_when_no_required_check_fails(monkeypatch) -> None:
    doctor = _load_doctor()
    _disable_checks(monkeypatch, doctor)

    assert doctor.main() == 0


def test_doctor_returns_nonzero_when_a_required_check_fails(monkeypatch) -> None:
    doctor = _load_doctor()
    _disable_checks(monkeypatch, doctor)
    monkeypatch.setattr(doctor, "check_host_model", lambda: doctor.fail("missing model"))

    assert doctor.main() == 1


def test_migration_check_prefers_the_running_compose_database(monkeypatch) -> None:
    doctor = _load_doctor()
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        if command == ["docker", "compose", "ps", "-q", "backend"]:
            return 0, "container-id"
        return 0, ""

    monkeypatch.setattr(doctor, "run", fake_run)
    doctor.check_migrations()

    assert commands == [
        ["docker", "compose", "ps", "-q", "backend"],
        ["docker", "compose", "exec", "-T", "backend", "python", "-m", "alembic", "check"],
    ]
