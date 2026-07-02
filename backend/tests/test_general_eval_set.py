"""Tests for the general English evaluation set (task 6.4).

These tests verify that the held-aside, versioned evaluation subset used by the
personalization guardrails (Req 6.2) loads deterministically: the same samples
in the same order, with a stable content digest, on every load.
"""

import json

import pytest

from app.ml.general_eval_set import (
    GENERAL_EVAL_SET_VERSION,
    EvalSample,
    GeneralEvalSet,
    general_eval_set_digest,
    load_general_eval_set,
)


def test_loads_and_is_non_empty():
    eval_set = load_general_eval_set()
    assert isinstance(eval_set, GeneralEvalSet)
    assert len(eval_set) > 0
    assert all(isinstance(s, EvalSample) for s in eval_set.samples)


def test_declared_version_matches_pinned_constant():
    eval_set = load_general_eval_set()
    assert eval_set.version == GENERAL_EVAL_SET_VERSION


def test_provenance_documents_source_and_split():
    prov = load_general_eval_set().provenance
    # Provenance must document where the set comes from and that it is held
    # aside from the test split (Req 6.2 / reproducibility).
    assert prov.get("base_dataset")
    assert prov.get("source_split") == "test"
    assert prov.get("selection_method")


def test_load_is_deterministic_in_order_and_content():
    # Two independent loads (bypassing the lru_cache via an explicit path)
    # produce identical, identically-ordered samples and the same digest.
    from app.ml.general_eval_set import MANIFEST_PATH

    a = load_general_eval_set(str(MANIFEST_PATH))
    b = load_general_eval_set(str(MANIFEST_PATH))
    assert a.sample_ids == b.sample_ids
    assert a.references() == b.references()
    assert a.digest == b.digest


def test_samples_sorted_by_id_regardless_of_manifest_order(tmp_path):
    # Even when the manifest lists samples out of order, the loader returns a
    # stable, sorted-by-id ordering so evaluation is reproducible.
    manifest = {
        "name": "general-english-eval",
        "version": GENERAL_EVAL_SET_VERSION,
        "provenance": {
            "base_dataset": "IAM",
            "source_split": "test",
            "selection_method": "test fixture",
        },
        "samples": [
            {"sample_id": "z99-001-00", "source_split": "test", "text": "zeta"},
            {"sample_id": "a01-000-00", "source_split": "test", "text": "alpha"},
            {"sample_id": "m05-010-02", "source_split": "test", "text": "mu"},
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    eval_set = load_general_eval_set(str(path))
    assert eval_set.sample_ids == ("a01-000-00", "m05-010-02", "z99-001-00")


def test_sample_ids_are_unique():
    ids = load_general_eval_set().sample_ids
    assert len(ids) == len(set(ids))


def test_digest_is_stable_hex_and_changes_with_content(tmp_path):
    digest1 = general_eval_set_digest()
    assert isinstance(digest1, str)
    # SHA-256 hex digest.
    assert len(digest1) == 64
    int(digest1, 16)  # parses as hex

    # A manifest with different content yields a different digest.
    manifest = {
        "name": "general-english-eval",
        "version": GENERAL_EVAL_SET_VERSION,
        "provenance": {"base_dataset": "IAM", "source_split": "test", "selection_method": "x"},
        "samples": [{"sample_id": "a01-000-00", "source_split": "test", "text": "different"}],
    }
    path = tmp_path / "other.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert load_general_eval_set(str(path)).digest != digest1


def test_version_mismatch_is_rejected(tmp_path):
    manifest = {
        "name": "general-english-eval",
        "version": "9.9.9",
        "provenance": {"base_dataset": "IAM", "source_split": "test", "selection_method": "x"},
        "samples": [{"sample_id": "a01-000-00", "source_split": "test", "text": "alpha"}],
    }
    path = tmp_path / "bad_version.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        load_general_eval_set(str(path))


def test_duplicate_sample_id_is_rejected(tmp_path):
    manifest = {
        "name": "general-english-eval",
        "version": GENERAL_EVAL_SET_VERSION,
        "provenance": {"base_dataset": "IAM", "source_split": "test", "selection_method": "x"},
        "samples": [
            {"sample_id": "a01-000-00", "source_split": "test", "text": "one"},
            {"sample_id": "a01-000-00", "source_split": "test", "text": "two"},
        ],
    }
    path = tmp_path / "dupes.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        load_general_eval_set(str(path))


def test_missing_manifest_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_general_eval_set(str(tmp_path / "does-not-exist.json"))
