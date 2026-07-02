"""Build and reproducibility smoke test (task 20.2).

Validates Requirements 11.1 and 11.2:

  11.1 – The System SHALL provide a containerized deployment definition that
         builds runnable frontend, backend, and recognition components from
         *pinned* dependency versions without requiring manual code modification
         during the build.

  11.2 – The System SHALL train the Base_Model from a documented public English
         handwriting dataset using a documented training procedure that specifies
         the dataset version, the data split, and the training configuration.

Test coverage
-------------
1. All required Docker deployment files exist in the project root.
2. ``docker-compose.yml`` declares the expected services (``backend``,
   ``frontend``).
3. ``docker-compose.yml`` references the partial-deployment env-var toggles
   required by Req 11.5 (``HND_STORAGE_AVAILABLE``,
   ``HND_HTR_ENGINE_AVAILABLE``, ``HND_TRAINING_WORKER_AVAILABLE``).
4. The base-model training script exists, imports cleanly, and exposes its
   documented public symbols.
5. The ``ML_BASE_MODEL_TRAINING.md`` / ``BASE_MODEL_TRAINING.md`` training
   documentation file exists.
6. ``backend/requirements.txt`` contains only *exact* pinned versions — no
   open-range operators (``>=``, ``<=``, ``~=``, ``^``, ``!=``).
7. ``frontend/package-lock.json`` exists, guaranteeing pinned frontend deps.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers: locate the project root from within backend/tests/
# ---------------------------------------------------------------------------

# backend/tests/ → backend/ → project root
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BACKEND_ROOT = _PROJECT_ROOT / "backend"
_FRONTEND_ROOT = _PROJECT_ROOT / "frontend"


# ---------------------------------------------------------------------------
# 1. Required Docker deployment files exist (Req 11.1)
# ---------------------------------------------------------------------------

REQUIRED_DEPLOYMENT_FILES = [
    "Dockerfile.backend",
    "Dockerfile.frontend",
    "docker-compose.yml",
    "nginx.conf",
    ".env.example",
]


@pytest.mark.parametrize("filename", REQUIRED_DEPLOYMENT_FILES)
def test_required_deployment_file_exists(filename: str) -> None:
    """Assert every required Docker/deployment file exists in the project root.

    Validates: Requirements 11.1
    """
    target = _PROJECT_ROOT / filename
    assert target.exists(), (
        f"Required deployment file '{filename}' is missing from the project "
        f"root ({_PROJECT_ROOT}). Requirement 11.1 demands all deployment files "
        "be present so the container builds from a clean checkout."
    )
    assert target.is_file(), (
        f"'{filename}' exists but is not a regular file."
    )


# ---------------------------------------------------------------------------
# 2. docker-compose.yml declares the expected services (Req 11.1)
# ---------------------------------------------------------------------------

REQUIRED_SERVICES = ["backend", "frontend"]


@pytest.mark.parametrize("service", REQUIRED_SERVICES)
def test_docker_compose_declares_required_service(service: str) -> None:
    """Assert docker-compose.yml lists the expected service names.

    Validates: Requirements 11.1
    """
    compose_path = _PROJECT_ROOT / "docker-compose.yml"
    content = compose_path.read_text(encoding="utf-8")

    # Match a YAML service key at the correct indentation level, e.g.:
    #   services:
    #     backend:
    pattern = re.compile(
        rf"^\s{{2,}}{re.escape(service)}\s*:", re.MULTILINE
    )
    assert pattern.search(content), (
        f"docker-compose.yml does not declare the '{service}' service. "
        "Requirement 11.1 requires runnable frontend and backend components."
    )


# ---------------------------------------------------------------------------
# 3. docker-compose.yml references partial-deployment env-var toggles (Req 11.5)
# ---------------------------------------------------------------------------

REQUIRED_ENV_VARS = [
    "HND_STORAGE_AVAILABLE",
    "HND_HTR_ENGINE_AVAILABLE",
    "HND_TRAINING_WORKER_AVAILABLE",
]


@pytest.mark.parametrize("env_var", REQUIRED_ENV_VARS)
def test_docker_compose_references_partial_deployment_toggle(env_var: str) -> None:
    """Assert docker-compose.yml references each partial-deployment env-var toggle.

    Validates: Requirements 11.1 (deployment definition), 11.5 (partial
    deployment with independently togglable components).
    """
    compose_path = _PROJECT_ROOT / "docker-compose.yml"
    content = compose_path.read_text(encoding="utf-8")

    assert env_var in content, (
        f"docker-compose.yml does not reference the env-var toggle '{env_var}'. "
        "Requirement 11.5 requires partial-deployment component toggles in the "
        "deployment definition."
    )


# ---------------------------------------------------------------------------
# 4a. base-model training script exists (Req 11.2)
# ---------------------------------------------------------------------------


def test_train_base_script_exists() -> None:
    """Assert the base-model training script is present in the expected location.

    Validates: Requirements 11.2
    """
    train_script = _BACKEND_ROOT / "app" / "ml" / "train_base.py"
    assert train_script.exists(), (
        f"Base-model training script not found at {train_script}. "
        "Requirement 11.2 requires a documented, runnable training procedure."
    )
    assert train_script.is_file()


# ---------------------------------------------------------------------------
# 4b. base-model training script imports cleanly and exposes required symbols
# ---------------------------------------------------------------------------


def test_train_base_imports_cleanly() -> None:
    """Assert app.ml.train_base imports without error (Req 11.2).

    A clean import verifies the module has no broken top-level code and that
    its documented public API is accessible.

    Validates: Requirements 11.2
    """
    # Ensure backend/ is on sys.path so app.* imports resolve.
    backend_str = str(_BACKEND_ROOT)
    added = False
    if backend_str not in sys.path:
        sys.path.insert(0, backend_str)
        added = True

    try:
        import importlib

        train_base = importlib.import_module("app.ml.train_base")

        # Verify the key documented public symbols are present.
        required_symbols = [
            "train_base_model",  # main entry point (Req 11.2)
            "IAM_DATASET_VERSION",  # pinned dataset version (Req 11.2)
            "DEFAULT_IAM_SPLIT",  # documented fixed split (Req 11.2)
            "LabeledSample",  # training data contract
            "TrainingProvenance",  # self-describing artifact record (Req 11.2)
            "TrainingResult",  # outcome type
            "load_base_model",  # runtime load helper
            "freeze_base_model",  # freezing helper
            "main",  # CLI entry point for the training procedure (Req 11.2)
        ]
        for symbol in required_symbols:
            assert hasattr(train_base, symbol), (
                f"app.ml.train_base is missing the required public symbol "
                f"'{symbol}'. This symbol is part of the documented training "
                "procedure (Requirement 11.2)."
            )

        # The documented dataset version must be a non-empty string.
        dataset_version = train_base.IAM_DATASET_VERSION
        assert isinstance(dataset_version, str) and dataset_version.strip(), (
            "IAM_DATASET_VERSION must be a non-empty string documenting the "
            "pinned dataset version (Requirement 11.2)."
        )

        # The training script must be runnable as a module (has a __name__
        # guard and main() callable).
        assert callable(train_base.main), (
            "app.ml.train_base.main must be callable (Requirement 11.2: "
            "training procedure is runnable via `python -m app.ml.train_base`)."
        )

    finally:
        if added:
            sys.path.remove(backend_str)


# ---------------------------------------------------------------------------
# 4c. train_base_model is runnable on a tiny synthetic dataset (Req 11.2)
# ---------------------------------------------------------------------------


def test_train_base_model_is_runnable_with_synthetic_data(tmp_path: Path) -> None:
    """Assert train_base_model completes without error on a tiny synthetic set.

    This confirms the training procedure is not just importable but actually
    *runnable* end-to-end (Requirement 11.2 requires a runnable training
    procedure).

    Validates: Requirements 11.2
    """
    backend_str = str(_BACKEND_ROOT)
    added = False
    if backend_str not in sys.path:
        sys.path.insert(0, backend_str)
        added = True

    try:
        import torch

        from app.config.settings import Settings
        from app.ml.htr_config import HTRModelConfig, TrainingConfig
        from app.ml.train_base import LabeledSample, train_base_model

        # Build a minimal but architecture-faithful config so the run is fast.
        # The CNN uses one 2×2 max-pool per stage; image_height / 2^stages must
        # equal 1, so with image_height=16 we need exactly 4 CNN stages.
        # (Same pattern as test_base_model_reproducibility.py.)
        config = HTRModelConfig(
            image_height=16,
            cnn_channels=(8, 16, 32, 32),
            lstm_hidden_size=8,
            lstm_num_layers=1,
            training=TrainingConfig(batch_size=2, max_epochs=5),
        )

        # Tiny synthetic dataset: the procedure must not need the real IAM download.
        gen = torch.Generator().manual_seed(42)
        samples = [
            LabeledSample(
                image=torch.rand(config.input_channels, config.image_height, 48, generator=gen),
                text=word,
            )
            for word in ["cat", "dog", "the", "note"]
        ]

        settings = Settings(object_store_path=str(tmp_path / "objectstore"))
        result = train_base_model(
            samples,
            config=config,
            max_epochs=1,
            settings=settings,
            save=False,  # skip disk writes; we only verify runnability
        )

        # The result must carry the frozen model and provenance.
        assert result.model is not None, "train_base_model must return a model."
        assert result.provenance is not None, (
            "train_base_model must return a TrainingProvenance record (Req 11.2)."
        )
        assert result.provenance.dataset_version, (
            "TrainingProvenance must record the dataset version (Req 11.2)."
        )
        assert result.provenance.split, (
            "TrainingProvenance must record the data split (Req 11.2)."
        )
        assert isinstance(result.provenance.training_config, dict), (
            "TrainingProvenance must record the training configuration (Req 11.2)."
        )

    finally:
        if added:
            sys.path.remove(backend_str)


# ---------------------------------------------------------------------------
# 5. Training documentation file exists (Req 11.2)
# ---------------------------------------------------------------------------

# The doc may live at app/ml/BASE_MODEL_TRAINING.md (implemented) or as a
# top-level ML_BASE_MODEL_TRAINING.md.  Accept either.
_TRAINING_DOC_CANDIDATES = [
    _BACKEND_ROOT / "app" / "ml" / "BASE_MODEL_TRAINING.md",
    _PROJECT_ROOT / "ML_BASE_MODEL_TRAINING.md",
    _BACKEND_ROOT / "app" / "ml" / "ML_BASE_MODEL_TRAINING.md",
    _PROJECT_ROOT / "BASE_MODEL_TRAINING.md",
]


def test_base_model_training_documentation_exists() -> None:
    """Assert the base-model training documentation file exists.

    Requirement 11.2 requires a documented training procedure that specifies
    the dataset version, the data split, and the training configuration.

    Validates: Requirements 11.2
    """
    found = [p for p in _TRAINING_DOC_CANDIDATES if p.exists() and p.is_file()]
    assert found, (
        "No base-model training documentation file was found. Checked:\n"
        + "\n".join(f"  {p}" for p in _TRAINING_DOC_CANDIDATES)
        + "\nRequirement 11.2 requires a documented training procedure."
    )

    # The documentation must mention the key required elements.
    doc_content = found[0].read_text(encoding="utf-8")
    for keyword in ("dataset", "split", "train"):
        assert keyword.lower() in doc_content.lower(), (
            f"Training documentation at '{found[0]}' does not mention "
            f"'{keyword}'. Requirement 11.2 requires documentation of the "
            "dataset version, data split, and training configuration."
        )


# ---------------------------------------------------------------------------
# 6. backend/requirements.txt contains only exact pinned versions (Req 11.1)
# ---------------------------------------------------------------------------

# Open-range version specifiers that would break reproducibility.
_OPEN_RANGE_PATTERN = re.compile(
    r"""
    (?:
        >=       |   # greater-than-or-equal
        <=       |   # less-than-or-equal (usually combined, e.g. >=1.0,<=2.0)
        ~=       |   # compatible release (PEP 440)
        \^       |   # caret range (npm/Poetry style; not PEP 440 but defensively caught)
        !=       |   # exclusion range (leaves other versions open)
        >(?!=)   |   # strict greater-than
        <(?!=)       # strict less-than (without '<=')
    )
    """,
    re.VERBOSE,
)


def test_requirements_txt_uses_exact_pinned_versions() -> None:
    """Assert backend/requirements.txt has no open-range version specifiers.

    All dependencies must be pinned with ``==`` so the container build is
    fully deterministic (Requirement 11.1).

    Validates: Requirements 11.1
    """
    req_path = _BACKEND_ROOT / "requirements.txt"
    assert req_path.exists(), (
        f"backend/requirements.txt not found at {req_path}."
    )

    violations: list[str] = []
    for lineno, raw_line in enumerate(req_path.read_text(encoding="utf-8").splitlines(), 1):
        # Strip inline comments and whitespace.
        line = raw_line.split("#")[0].strip()
        if not line:
            continue
        # Skip option flags such as -r, --index-url, -e, etc.
        if line.startswith("-"):
            continue
        if _OPEN_RANGE_PATTERN.search(line):
            violations.append(f"  line {lineno}: {raw_line.rstrip()}")

    assert not violations, (
        "backend/requirements.txt contains open-range version specifiers "
        "(Requirement 11.1 requires exact pinning for reproducibility):\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# 7. frontend/package-lock.json exists (Req 11.1)
# ---------------------------------------------------------------------------


def test_frontend_package_lock_exists() -> None:
    """Assert frontend/package-lock.json exists, guaranteeing pinned frontend deps.

    npm generates package-lock.json with exact resolved versions for every
    transitive dependency; its presence proves the frontend build is
    deterministic (Requirement 11.1).

    Validates: Requirements 11.1
    """
    lockfile = _FRONTEND_ROOT / "package-lock.json"
    assert lockfile.exists(), (
        f"frontend/package-lock.json not found at {lockfile}. "
        "Requirement 11.1 requires pinned dependency versions for the frontend. "
        "Run `npm install` in the frontend directory to generate it."
    )
    assert lockfile.is_file(), (
        "frontend/package-lock.json exists but is not a regular file."
    )
    # Verify the file is non-empty and contains at least the lockfileVersion key.
    content = lockfile.read_text(encoding="utf-8")
    assert "lockfileVersion" in content, (
        "frontend/package-lock.json appears to be empty or malformed "
        "(expected 'lockfileVersion' key)."
    )
