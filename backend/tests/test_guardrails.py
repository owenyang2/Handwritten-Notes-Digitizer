"""Tests for guardrail logic and complete_training_run (Task 14.5).

Covers:
- GuardrailResult enum values
- evaluate_guardrail: boundary/threshold correctness (Req 6.3, 6.4)
- complete_training_run: ACCEPTED / REJECTED / FAILED paths, consumed-pair
  marking, adapter upsert, EvaluationResult persistence, notification content
  (Req 5.7, 6.3, 6.4, 6.6, 9.6, 10.2)
- Property 18: Adapter accept/reject guardrail decision (Req 6.3, 6.4)

# Feature: handwriting-notes-digitizer, Property 18: Adapter accept/reject guardrail decision
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.data.enums import AdapterStatus, TrainingRunState
from app.services.personalization_service import (
    GuardrailResult,
    TrainingOutcome,
    TrainingRunState,
    evaluate_guardrail,
    complete_training_run,
)


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

def _uid() -> uuid.UUID:
    return uuid.uuid4()


@dataclass
class FakeGeneralEval:
    """Duck-typed stand-in for a BeforeAfterReport on the general eval set."""
    before_cer: Optional[float] = 0.10
    before_wer: Optional[float] = 0.20
    after_cer: Optional[float] = 0.10
    after_wer: Optional[float] = 0.20
    delta_cer: Optional[float] = 0.0
    delta_wer: Optional[float] = 0.0
    availability: str = "available"


@dataclass
class FakeAdapterResult:
    """Minimal stand-in for AdapterResult."""
    classifier_weight: object = None
    classifier_bias: object = None
    epochs_run: int = 5
    cer_history: list = field(default_factory=list)


def _make_repos(
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    run_state: TrainingRunState = TrainingRunState.RUNNING,
    pair_ids: Optional[list[uuid.UUID]] = None,
    has_adapter: bool = False,
) -> MagicMock:
    """Build a minimal RepositoryFactory mock for complete_training_run tests."""
    repos = MagicMock()

    # TrainingRun
    run = MagicMock()
    run.run_id = run_id
    run.user_id = user_id
    run.state = run_state
    run.run_pairs = []  # populated if pair_ids given
    repos.training_runs.get.return_value = run
    repos.training_runs.session = MagicMock()

    # TrainingPairs
    pairs = {}
    if pair_ids:
        for pid in pair_ids:
            p = MagicMock()
            p.pair_id = pid
            p.user_id = user_id
            p.consumed_by_run_id = None
            pairs[pid] = p
            rp = MagicMock()
            rp.pair_id = pid
            run.run_pairs.append(rp)
    repos.training_pairs.get.side_effect = lambda uid, pid: pairs.get(pid)
    repos.training_pairs.session = MagicMock()

    # Adapter
    if has_adapter:
        adapter = MagicMock()
        adapter.adapter_id = uuid.uuid4()
        adapter.user_id = user_id
        adapter.status = AdapterStatus.ACTIVE
        repos.adapters.get_for_user.return_value = adapter
        repos.adapters.get_active_for_user.return_value = adapter
        repos.adapters.session = MagicMock()
    else:
        repos.adapters.get_for_user.return_value = None
        repos.adapters.get_active_for_user.return_value = None
        repos.adapters.session = MagicMock()

    # EvaluationResult
    repos.evaluation_results.add = MagicMock(return_value=MagicMock())

    # Adapter add
    def _adapter_add(a):
        a.adapter_id = uuid.uuid4()
        return a
    repos.adapters.add.side_effect = _adapter_add

    return repos, pairs


def _make_object_store() -> MagicMock:
    store = MagicMock()
    store.put_adapter_weights.return_value = "adapters/user/weights.pt"
    return store


# ---------------------------------------------------------------------------
# Unit tests: evaluate_guardrail
# ---------------------------------------------------------------------------

class TestEvaluateGuardrail:
    """Boundary-condition tests for the guardrail decision function."""

    def test_both_within_tolerance_accepted(self):
        result = evaluate_guardrail(
            adapter_cer=0.10, base_cer=0.10,
            adapter_wer=0.20, base_wer=0.20,
        )
        assert result == GuardrailResult.ACCEPTED

    def test_adapter_cer_exactly_at_tolerance_accepted(self):
        # adapter_cer == base_cer + 0.01  →  accepted (≤ not >)
        result = evaluate_guardrail(
            adapter_cer=0.11, base_cer=0.10,
            adapter_wer=0.20, base_wer=0.20,
        )
        assert result == GuardrailResult.ACCEPTED

    def test_adapter_cer_just_above_tolerance_rejected(self):
        # 0.1001 > 0.10 + 0.01 = 0.11  → False; let's use a clear violation
        result = evaluate_guardrail(
            adapter_cer=0.111, base_cer=0.10,
            adapter_wer=0.20, base_wer=0.20,
        )
        assert result == GuardrailResult.REJECTED_CER

    def test_adapter_wer_exactly_at_tolerance_accepted(self):
        # adapter_wer == base_wer + 0.02 → accepted
        result = evaluate_guardrail(
            adapter_cer=0.10, base_cer=0.10,
            adapter_wer=0.22, base_wer=0.20,
        )
        assert result == GuardrailResult.ACCEPTED

    def test_adapter_wer_just_above_tolerance_rejected(self):
        result = evaluate_guardrail(
            adapter_cer=0.10, base_cer=0.10,
            adapter_wer=0.221, base_wer=0.20,
        )
        assert result == GuardrailResult.REJECTED_WER

    def test_cer_violation_takes_precedence_over_wer(self):
        # Both violated: CER is checked first.
        result = evaluate_guardrail(
            adapter_cer=0.15, base_cer=0.10,
            adapter_wer=0.30, base_wer=0.20,
        )
        assert result == GuardrailResult.REJECTED_CER

    def test_improved_cer_and_wer_accepted(self):
        # Adapter better than base on both metrics.
        result = evaluate_guardrail(
            adapter_cer=0.05, base_cer=0.10,
            adapter_wer=0.10, base_wer=0.20,
        )
        assert result == GuardrailResult.ACCEPTED

    def test_cer_within_wer_violated(self):
        result = evaluate_guardrail(
            adapter_cer=0.10, base_cer=0.10,
            adapter_wer=0.25, base_wer=0.20,
        )
        assert result == GuardrailResult.REJECTED_WER

    def test_zero_base_zero_adapter_accepted(self):
        result = evaluate_guardrail(0.0, 0.0, 0.0, 0.0)
        assert result == GuardrailResult.ACCEPTED

    def test_large_regression_rejected_cer(self):
        result = evaluate_guardrail(
            adapter_cer=0.50, base_cer=0.10,
            adapter_wer=0.50, base_wer=0.20,
        )
        assert result == GuardrailResult.REJECTED_CER


# ---------------------------------------------------------------------------
# Unit tests: complete_training_run – FAILED path
# ---------------------------------------------------------------------------

class TestCompleteTrainingRunFailed:
    """When adapter_result or general_eval is None → FAILED outcome."""

    def test_none_adapter_result_gives_failed(self):
        uid = _uid()
        rid = _uid()
        repos, _ = _make_repos(uid, rid)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=None,
            general_eval=FakeGeneralEval(),
            repos=repos, object_store=store,
        )
        assert outcome.accepted is False
        assert outcome.run_final_state == TrainingRunState.FAILED
        assert outcome.guardrail_result is None

    def test_none_general_eval_gives_failed(self):
        uid = _uid()
        rid = _uid()
        repos, _ = _make_repos(uid, rid)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=None,
            repos=repos, object_store=store,
        )
        assert outcome.accepted is False
        assert outcome.run_final_state == TrainingRunState.FAILED

    def test_failed_retains_prior_adapter(self):
        uid = _uid()
        rid = _uid()
        repos, _ = _make_repos(uid, rid, has_adapter=True)
        prior_adapter_id = repos.adapters.get_for_user(uid).adapter_id
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=None,
            general_eval=FakeGeneralEval(),
            repos=repos, object_store=store,
        )
        # Adapter is unchanged (add was NOT called)
        repos.adapters.add.assert_not_called()
        assert outcome.adapter_id == prior_adapter_id

    def test_failed_notification_is_informative(self):
        uid = _uid()
        rid = _uid()
        repos, _ = _make_repos(uid, rid)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=None,
            general_eval=FakeGeneralEval(),
            repos=repos, object_store=store,
        )
        assert len(outcome.notification) > 0

    def test_failed_does_not_mark_pairs_consumed(self):
        uid = _uid()
        rid = _uid()
        pair_ids = [_uid(), _uid()]
        repos, pairs = _make_repos(uid, rid, pair_ids=pair_ids)
        store = _make_object_store()

        complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=None,
            general_eval=FakeGeneralEval(),
            repos=repos, object_store=store,
        )
        # No pair should have consumed_by_run_id set
        for p in pairs.values():
            assert p.consumed_by_run_id is None


# ---------------------------------------------------------------------------
# Unit tests: complete_training_run – REJECTED path
# ---------------------------------------------------------------------------

class TestCompleteTrainingRunRejected:
    """When guardrail fails → REJECTED outcome; prior adapter retained."""

    def _rejected_eval(self) -> FakeGeneralEval:
        """Returns an eval that triggers CER rejection."""
        return FakeGeneralEval(
            before_cer=0.10, before_wer=0.20,
            after_cer=0.15, after_wer=0.20,  # CER +5pp → rejected
            delta_cer=-0.05, delta_wer=0.0,
            availability="available",
        )

    def test_rejected_outcome_not_accepted(self):
        uid = _uid()
        rid = _uid()
        repos, _ = _make_repos(uid, rid)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=self._rejected_eval(),
            repos=repos, object_store=store,
        )
        assert outcome.accepted is False
        assert outcome.run_final_state == TrainingRunState.REJECTED

    def test_rejected_cer_guardrail_result(self):
        uid = _uid()
        rid = _uid()
        repos, _ = _make_repos(uid, rid)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=self._rejected_eval(),
            repos=repos, object_store=store,
        )
        assert outcome.guardrail_result == GuardrailResult.REJECTED_CER

    def test_rejected_retains_prior_adapter(self):
        uid = _uid()
        rid = _uid()
        repos, _ = _make_repos(uid, rid, has_adapter=True)
        prior_id = repos.adapters.get_for_user(uid).adapter_id
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=self._rejected_eval(),
            repos=repos, object_store=store,
        )
        # New adapter was NOT added
        repos.adapters.add.assert_not_called()
        assert outcome.adapter_id == prior_id

    def test_rejected_notification_mentions_accuracy(self):
        uid = _uid()
        rid = _uid()
        repos, _ = _make_repos(uid, rid)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=self._rejected_eval(),
            repos=repos, object_store=store,
        )
        # Req 6.6: user must be notified on rejection
        assert "recognition" in outcome.notification.lower() or "accuracy" in outcome.notification.lower()
        assert len(outcome.notification) > 0

    def test_rejected_persists_eval_result(self):
        uid = _uid()
        rid = _uid()
        repos, _ = _make_repos(uid, rid)
        store = _make_object_store()

        complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=self._rejected_eval(),
            repos=repos, object_store=store,
        )
        repos.evaluation_results.add.assert_called_once()

    def test_rejected_does_not_mark_pairs_consumed(self):
        uid = _uid()
        rid = _uid()
        pair_ids = [_uid(), _uid()]
        repos, pairs = _make_repos(uid, rid, pair_ids=pair_ids)
        store = _make_object_store()

        complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=self._rejected_eval(),
            repos=repos, object_store=store,
        )
        # Pairs must NOT be marked consumed on rejection (Req 6.3 / 10.2)
        for p in pairs.values():
            assert p.consumed_by_run_id is None

    def test_rejected_wer_gives_rejected_wer_reason(self):
        uid = _uid()
        rid = _uid()
        repos, _ = _make_repos(uid, rid)
        store = _make_object_store()

        wer_rejected_eval = FakeGeneralEval(
            before_cer=0.10, before_wer=0.20,
            after_cer=0.10, after_wer=0.25,  # WER +5pp
            delta_cer=0.0, delta_wer=-0.05,
            availability="available",
        )

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=wer_rejected_eval,
            repos=repos, object_store=store,
        )
        assert outcome.guardrail_result == GuardrailResult.REJECTED_WER
        assert outcome.run_final_state == TrainingRunState.REJECTED


# ---------------------------------------------------------------------------
# Unit tests: complete_training_run – ACCEPTED path
# ---------------------------------------------------------------------------

class TestCompleteTrainingRunAccepted:
    """When guardrail passes → ACCEPTED outcome; adapter saved, pairs consumed."""

    def _good_eval(self) -> FakeGeneralEval:
        return FakeGeneralEval(
            before_cer=0.10, before_wer=0.20,
            after_cer=0.09, after_wer=0.19,
            delta_cer=0.01, delta_wer=0.01,
            availability="available",
        )

    def test_accepted_outcome(self):
        uid = _uid()
        rid = _uid()
        repos, _ = _make_repos(uid, rid)
        store = _make_object_store()

        outcome = complete_training_run(
            user_id=uid, run_id=rid,
            adapter_result=FakeAdapterResult(),
            general_eval=self._good_eval(),
            repos=repos, object_store=store,
        )
        assert outcome.accepted is True
        assert outcome.run_final_state == TrainingRunState.ACCEPTED
        assert outcome.guardrail_result == GuardrailResult.ACCEPTED

    def test_accepted_saves_weights_to_object_store(self):
        uid = _uid()
        rid = _uid()
        repos, _ = _make_repos(uid, rid)
        store = _make_object_store()

        with patch(
            "app.services.personalization_service.adapter_result_to_bytes",
            return_value=b"fake_weights",
        ):
            complete_training_run(
                user_id=uid, run_id=rid,
                adapter_result=FakeAdapterResult(),
                general_eval=self._good_eval(),
                repos=repos, object_store=store,
            )

        store.put_adapter_weights.assert_called_once()

    def test_accepted_creates_adapter_when_none_exists(self):
        uid = _uid()
        rid = _uid()
        repos, _ = _make_repos(uid, rid, has_adapter=False)
        store = _make_object_store()

        with patch(
            "app.services.personalization_service.adapter_result_to_bytes",
            return_value=b"w",
        ):
            outcome = complete_training_run(
                user_id=uid, run_id=rid,
                adapter_result=FakeAdapterResult(),
                general_eval=self._good_eval(),
                repos=repos, object_store=store,
            )

        repos.adapters.add.assert_called_once()
        assert outcome.adapter_id is not None

    def test_accepted_updates_existing_adapter(self):
        uid = _uid()
        rid = _uid()
        repos, _ = _make_repos(uid, rid, has_adapter=True)
        prior_id = repos.adapters.get_for_user(uid).adapter_id
        store = _make_object_store()

        with patch(
            "app.services.personalization_service.adapter_result_to_bytes",
            return_value=b"w",
        ):
            outcome = complete_training_run(
                user_id=uid, run_id=rid,
                adapter_result=FakeAdapterResult(),
                general_eval=self._good_eval(),
                repos=repos, object_store=store,
            )

        # Existing adapter updated in place — add NOT called
        repos.adapters.add.assert_not_called()
        assert outcome.adapter_id == prior_id

    def test_accepted_marks_pairs_consumed(self):
        uid = _uid()
        rid = _uid()
        pair_ids = [_uid(), _uid(), _uid()]
        repos, pairs = _make_repos(uid, rid, pair_ids=pair_ids)
        store = _make_object_store()

        with patch(
            "app.services.personalization_service.adapter_result_to_bytes",
            return_value=b"w",
        ):
            complete_training_run(
                user_id=uid, run_id=rid,
                adapter_result=FakeAdapterResult(),
                general_eval=self._good_eval(),
                repos=repos, object_store=store,
            )

        for p in pairs.values():
            assert p.consumed_by_run_id == rid, (
                f"Pair {p.pair_id} should be marked consumed on acceptance"
            )

    def test_accepted_marks_explicit_pair_ids_consumed(self):
        """When pair_ids is explicitly provided they are marked consumed."""
        uid = _uid()
        rid = _uid()
        pair_ids = [_uid(), _uid()]
        repos, pairs = _make_repos(uid, rid, pair_ids=pair_ids)
        store = _make_object_store()

        with patch(
            "app.services.personalization_service.adapter_result_to_bytes",
            return_value=b"w",
        ):
            complete_training_run(
                user_id=uid, run_id=rid,
                adapter_result=FakeAdapterResult(),
                general_eval=self._good_eval(),
                repos=repos, object_store=store,
                pair_ids=pair_ids,
            )

        for p in pairs.values():
            assert p.consumed_by_run_id == rid

    def test_accepted_persists_eval_result(self):
        uid = _uid()
        rid = _uid()
        repos, _ = _make_repos(uid, rid)
        store = _make_object_store()

        with patch(
            "app.services.personalization_service.adapter_result_to_bytes",
            return_value=b"w",
        ):
            complete_training_run(
                user_id=uid, run_id=rid,
                adapter_result=FakeAdapterResult(),
                general_eval=self._good_eval(),
                repos=repos, object_store=store,
            )

        repos.evaluation_results.add.assert_called_once()

    def test_accepted_notification_is_informative(self):
        uid = _uid()
        rid = _uid()
        repos, _ = _make_repos(uid, rid)
        store = _make_object_store()

        with patch(
            "app.services.personalization_service.adapter_result_to_bytes",
            return_value=b"w",
        ):
            outcome = complete_training_run(
                user_id=uid, run_id=rid,
                adapter_result=FakeAdapterResult(),
                general_eval=self._good_eval(),
                repos=repos, object_store=store,
            )

        assert len(outcome.notification) > 0


# ---------------------------------------------------------------------------
# Property 18: Adapter accept/reject guardrail decision (Req 6.3, 6.4)
#
# Feature: handwriting-notes-digitizer, Property 18: Adapter accept/reject
# guardrail decision
# ---------------------------------------------------------------------------

@st.composite
def guardrail_inputs(draw):
    """Generate (adapter_cer, base_cer, adapter_wer, base_wer) tuples."""
    base_cer = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))
    base_wer = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))
    adapter_cer = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))
    adapter_wer = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))
    return adapter_cer, base_cer, adapter_wer, base_wer


# Feature: handwriting-notes-digitizer, Property 18: Adapter accept/reject guardrail decision
@given(guardrail_inputs())
@settings(max_examples=25)
def test_property_18_guardrail_decision(inputs):
    """Property 18: accept iff CER diff ≤ 1.0 pp AND WER diff ≤ 2.0 pp.

    **Validates: Requirements 6.3, 6.4**
    """
    adapter_cer, base_cer, adapter_wer, base_wer = inputs

    result = evaluate_guardrail(adapter_cer, base_cer, adapter_wer, base_wer)

    cer_ok = adapter_cer <= base_cer + 0.01
    wer_ok = adapter_wer <= base_wer + 0.02

    if cer_ok and wer_ok:
        # Must accept.
        assert result == GuardrailResult.ACCEPTED, (
            f"Expected ACCEPTED but got {result} for "
            f"adapter_cer={adapter_cer}, base_cer={base_cer}, "
            f"adapter_wer={adapter_wer}, base_wer={base_wer}"
        )
    else:
        # Must reject.
        assert result in (GuardrailResult.REJECTED_CER, GuardrailResult.REJECTED_WER), (
            f"Expected REJECTED_* but got {result} for "
            f"adapter_cer={adapter_cer}, base_cer={base_cer}, "
            f"adapter_wer={adapter_wer}, base_wer={base_wer}"
        )
        # If CER is violated, must report REJECTED_CER.
        if not cer_ok:
            assert result == GuardrailResult.REJECTED_CER, (
                f"CER violated but result was {result}"
            )
        # If CER is OK but WER is violated, must report REJECTED_WER.
        elif not wer_ok:
            assert result == GuardrailResult.REJECTED_WER, (
                f"WER violated (CER OK) but result was {result}"
            )
