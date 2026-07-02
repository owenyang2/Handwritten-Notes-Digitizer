"""Unit tests for guardrail-rejection side effects (Task 14.16).

Requirement 6.6: WHEN the Personalization_Service rejects a newly trained
Adapter, THE Personalization_Service SHALL record an indication that
personalization did not improve recognition and notify the user.

These tests verify that when ``complete_training_run`` is called with metrics
that fail the guardrail:
  - The outcome has ``accepted=False``.
  - The outcome has a non-empty ``notification`` message indicating rejection.
  - The ``TrainingRun`` in the DB is marked with a rejection state
    (REJECTED_CER or REJECTED_WER).
  - The user's prior adapter (or ``None`` if absent) is unchanged.

CER rejection and WER rejection are tested separately.

_Requirements: 6.6_
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock

import pytest

from app.data.enums import AdapterStatus, TrainingRunState
from app.services.personalization_service import (
    GuardrailResult,
    TrainingOutcome,
    complete_training_run,
)


# ---------------------------------------------------------------------------
# Helpers / fakes (mirror the pattern established in test_guardrails.py)
# ---------------------------------------------------------------------------


def _uid() -> uuid.UUID:
    return uuid.uuid4()


@dataclass
class FakeAdapterResult:
    """Minimal stand-in for ``AdapterResult``."""
    classifier_weight: object = None
    classifier_bias: object = None
    epochs_run: int = 5
    cer_history: list = field(default_factory=list)


@dataclass
class FakeGeneralEval:
    """Duck-typed stand-in for a ``BeforeAfterReport`` on the general eval set."""
    before_cer: Optional[float] = 0.10
    before_wer: Optional[float] = 0.20
    after_cer: Optional[float] = 0.10
    after_wer: Optional[float] = 0.20
    delta_cer: Optional[float] = 0.0
    delta_wer: Optional[float] = 0.0
    availability: str = "available"


def _cer_rejected_eval() -> FakeGeneralEval:
    """Eval that triggers CER rejection: adapter CER exceeds base CER + 1.0 pp."""
    return FakeGeneralEval(
        before_cer=0.10,
        before_wer=0.20,
        after_cer=0.15,   # +5 pp → rejected
        after_wer=0.20,
        delta_cer=-0.05,
        delta_wer=0.0,
        availability="available",
    )


def _wer_rejected_eval() -> FakeGeneralEval:
    """Eval that triggers WER rejection: adapter WER exceeds base WER + 2.0 pp."""
    return FakeGeneralEval(
        before_cer=0.10,
        before_wer=0.20,
        after_cer=0.10,   # CER OK
        after_wer=0.25,   # +5 pp → rejected
        delta_cer=0.0,
        delta_wer=-0.05,
        availability="available",
    )


def _make_repos(
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    has_prior_adapter: bool = False,
    prior_adapter_id: Optional[uuid.UUID] = None,
) -> MagicMock:
    """Build a minimal RepositoryFactory mock for ``complete_training_run``."""
    repos = MagicMock()

    # --- TrainingRun ---
    run = MagicMock()
    run.run_id = run_id
    run.user_id = user_id
    run.state = TrainingRunState.RUNNING
    run.run_pairs = []
    repos.training_runs.get.return_value = run
    repos.training_runs.session = MagicMock()

    # --- TrainingPairs (none needed for these tests) ---
    repos.training_pairs.get.return_value = None
    repos.training_pairs.session = MagicMock()

    # --- Adapter ---
    if has_prior_adapter:
        adapter = MagicMock()
        adapter.adapter_id = prior_adapter_id or uuid.uuid4()
        adapter.user_id = user_id
        adapter.status = AdapterStatus.ACTIVE
        repos.adapters.get_for_user.return_value = adapter
        repos.adapters.get_active_for_user.return_value = adapter
    else:
        repos.adapters.get_for_user.return_value = None
        repos.adapters.get_active_for_user.return_value = None
    repos.adapters.session = MagicMock()
    repos.adapters.add = MagicMock()

    # --- EvaluationResult ---
    repos.evaluation_results.add = MagicMock(return_value=MagicMock())

    return repos, repos.training_runs.get.return_value  # (repos, run_mock)


def _make_object_store() -> MagicMock:
    store = MagicMock()
    store.put_adapter_weights.return_value = "adapters/user/weights.pt"
    return store


# ---------------------------------------------------------------------------
# CER rejection tests
# ---------------------------------------------------------------------------


class TestCERRejectionSideEffects:
    """Verify all side effects when the adapter is rejected due to CER violation."""

    def test_cer_rejection_outcome_is_not_accepted(self):
        """Outcome.accepted must be False when CER guardrail fires (Req 6.6)."""
        uid = _uid()
        rid = _uid()
        repos, run = _make_repos(uid, rid)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_cer_rejected_eval(),
            repos=repos, object_store=store,
        )

        assert outcome.accepted is False

    def test_cer_rejection_guardrail_result_is_rejected_cer(self):
        """guardrail_result must be REJECTED_CER on a CER violation (Req 6.3)."""
        uid = _uid()
        rid = _uid()
        repos, run = _make_repos(uid, rid)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_cer_rejected_eval(),
            repos=repos, object_store=store,
        )

        assert outcome.guardrail_result == GuardrailResult.REJECTED_CER

    def test_cer_rejection_run_state_is_rejected(self):
        """TrainingRun must be marked REJECTED after a CER guardrail rejection (Req 6.6)."""
        uid = _uid()
        rid = _uid()
        repos, run = _make_repos(uid, rid)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_cer_rejected_eval(),
            repos=repos, object_store=store,
        )

        # The run mock's state attribute should have been mutated to REJECTED.
        assert run.state == TrainingRunState.REJECTED
        assert outcome.run_final_state == TrainingRunState.REJECTED

    def test_cer_rejection_notification_is_non_empty(self):
        """A non-empty notification must be produced on rejection (Req 6.6)."""
        uid = _uid()
        rid = _uid()
        repos, run = _make_repos(uid, rid)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_cer_rejected_eval(),
            repos=repos, object_store=store,
        )

        assert isinstance(outcome.notification, str)
        assert len(outcome.notification) > 0

    def test_cer_rejection_notification_indicates_no_improvement(self):
        """Notification text must communicate that personalization did not improve (Req 6.6)."""
        uid = _uid()
        rid = _uid()
        repos, run = _make_repos(uid, rid)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_cer_rejected_eval(),
            repos=repos, object_store=store,
        )

        msg_lower = outcome.notification.lower()
        # Message must convey the idea that recognition did not improve.
        assert (
            "did not improve" in msg_lower
            or "not improve" in msg_lower
            or "recognition" in msg_lower
            or "accuracy" in msg_lower
        ), f"Notification does not indicate non-improvement: {outcome.notification!r}"

    def test_cer_rejection_prior_adapter_id_unchanged(self):
        """User's prior adapter must be retained (not replaced) on CER rejection (Req 6.6)."""
        uid = _uid()
        rid = _uid()
        prior_id = uuid.uuid4()
        repos, run = _make_repos(uid, rid, has_prior_adapter=True, prior_adapter_id=prior_id)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_cer_rejected_eval(),
            repos=repos, object_store=store,
        )

        # No new adapter should have been added.
        repos.adapters.add.assert_not_called()
        # The returned adapter_id must match the prior adapter.
        assert outcome.adapter_id == prior_id

    def test_cer_rejection_no_prior_adapter_remains_none(self):
        """When the user has no prior adapter, adapter_id must remain None on rejection."""
        uid = _uid()
        rid = _uid()
        repos, run = _make_repos(uid, rid, has_prior_adapter=False)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_cer_rejected_eval(),
            repos=repos, object_store=store,
        )

        repos.adapters.add.assert_not_called()
        assert outcome.adapter_id is None

    def test_cer_rejection_records_did_not_improve_in_run(self):
        """The run's reject_reason must be set to record the rejection evidence (Req 6.6)."""
        uid = _uid()
        rid = _uid()
        repos, run = _make_repos(uid, rid)
        store = _make_object_store()

        complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_cer_rejected_eval(),
            repos=repos, object_store=store,
        )

        # reject_reason must be set to a non-empty string.
        assert run.reject_reason is not None
        assert len(str(run.reject_reason)) > 0

    def test_cer_rejection_persists_evaluation_result(self):
        """An EvaluationResult must be persisted even on rejection (Req 6.6)."""
        uid = _uid()
        rid = _uid()
        repos, run = _make_repos(uid, rid)
        store = _make_object_store()

        complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_cer_rejected_eval(),
            repos=repos, object_store=store,
        )

        repos.evaluation_results.add.assert_called_once()

    def test_cer_rejection_does_not_save_new_adapter_weights(self):
        """Object store must NOT be written on rejection (adapter is not activated)."""
        uid = _uid()
        rid = _uid()
        repos, run = _make_repos(uid, rid)
        store = _make_object_store()

        complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_cer_rejected_eval(),
            repos=repos, object_store=store,
        )

        store.put_adapter_weights.assert_not_called()


# ---------------------------------------------------------------------------
# WER rejection tests
# ---------------------------------------------------------------------------


class TestWERRejectionSideEffects:
    """Verify all side effects when the adapter is rejected due to WER violation."""

    def test_wer_rejection_outcome_is_not_accepted(self):
        """Outcome.accepted must be False when WER guardrail fires (Req 6.6)."""
        uid = _uid()
        rid = _uid()
        repos, run = _make_repos(uid, rid)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_wer_rejected_eval(),
            repos=repos, object_store=store,
        )

        assert outcome.accepted is False

    def test_wer_rejection_guardrail_result_is_rejected_wer(self):
        """guardrail_result must be REJECTED_WER on a WER violation (Req 6.4)."""
        uid = _uid()
        rid = _uid()
        repos, run = _make_repos(uid, rid)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_wer_rejected_eval(),
            repos=repos, object_store=store,
        )

        assert outcome.guardrail_result == GuardrailResult.REJECTED_WER

    def test_wer_rejection_run_state_is_rejected(self):
        """TrainingRun must be marked REJECTED after a WER guardrail rejection (Req 6.6)."""
        uid = _uid()
        rid = _uid()
        repos, run = _make_repos(uid, rid)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_wer_rejected_eval(),
            repos=repos, object_store=store,
        )

        assert run.state == TrainingRunState.REJECTED
        assert outcome.run_final_state == TrainingRunState.REJECTED

    def test_wer_rejection_notification_is_non_empty(self):
        """A non-empty notification must be produced on WER rejection (Req 6.6)."""
        uid = _uid()
        rid = _uid()
        repos, run = _make_repos(uid, rid)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_wer_rejected_eval(),
            repos=repos, object_store=store,
        )

        assert isinstance(outcome.notification, str)
        assert len(outcome.notification) > 0

    def test_wer_rejection_notification_indicates_no_improvement(self):
        """Notification text must communicate that personalization did not improve (Req 6.6)."""
        uid = _uid()
        rid = _uid()
        repos, run = _make_repos(uid, rid)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_wer_rejected_eval(),
            repos=repos, object_store=store,
        )

        msg_lower = outcome.notification.lower()
        assert (
            "did not improve" in msg_lower
            or "not improve" in msg_lower
            or "recognition" in msg_lower
            or "accuracy" in msg_lower
        ), f"Notification does not indicate non-improvement: {outcome.notification!r}"

    def test_wer_rejection_prior_adapter_id_unchanged(self):
        """User's prior adapter must be retained on WER rejection (Req 6.6)."""
        uid = _uid()
        rid = _uid()
        prior_id = uuid.uuid4()
        repos, run = _make_repos(uid, rid, has_prior_adapter=True, prior_adapter_id=prior_id)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_wer_rejected_eval(),
            repos=repos, object_store=store,
        )

        repos.adapters.add.assert_not_called()
        assert outcome.adapter_id == prior_id

    def test_wer_rejection_no_prior_adapter_remains_none(self):
        """When the user has no prior adapter, adapter_id must remain None on WER rejection."""
        uid = _uid()
        rid = _uid()
        repos, run = _make_repos(uid, rid, has_prior_adapter=False)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_wer_rejected_eval(),
            repos=repos, object_store=store,
        )

        repos.adapters.add.assert_not_called()
        assert outcome.adapter_id is None

    def test_wer_rejection_records_did_not_improve_in_run(self):
        """The run's reject_reason must be set on WER rejection (Req 6.6)."""
        uid = _uid()
        rid = _uid()
        repos, run = _make_repos(uid, rid)
        store = _make_object_store()

        complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_wer_rejected_eval(),
            repos=repos, object_store=store,
        )

        assert run.reject_reason is not None
        assert len(str(run.reject_reason)) > 0

    def test_wer_rejection_persists_evaluation_result(self):
        """An EvaluationResult must be persisted even on WER rejection (Req 6.6)."""
        uid = _uid()
        rid = _uid()
        repos, run = _make_repos(uid, rid)
        store = _make_object_store()

        complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_wer_rejected_eval(),
            repos=repos, object_store=store,
        )

        repos.evaluation_results.add.assert_called_once()

    def test_wer_rejection_does_not_save_new_adapter_weights(self):
        """Object store must NOT be written on WER rejection."""
        uid = _uid()
        rid = _uid()
        repos, run = _make_repos(uid, rid)
        store = _make_object_store()

        complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=_wer_rejected_eval(),
            repos=repos, object_store=store,
        )

        store.put_adapter_weights.assert_not_called()
