"""Unit tests for CER and WER metrics in evaluation_service.py.

Covers:
- Exact match → 0.0
- Empty reference → None (unavailable, Req 7.6)
- Whitespace-only reference → None for WER (no words), 0.0 for CER (has chars)
- Single substitution, insertion, deletion
- All-wrong hypothesis
- Unicode characters
- Long strings (smoke test for correctness and non-negativity)

Validates: Requirements 7.4, 7.5, 7.6
"""
import pytest

from app.services.evaluation_service import cer, wer


# ---------------------------------------------------------------------------
# CER unit tests
# ---------------------------------------------------------------------------


class TestCER:
    """Unit tests for the cer() function."""

    def test_identical_strings_returns_zero(self):
        assert cer("hello world", "hello world") == 0.0

    def test_empty_reference_returns_none(self):
        assert cer("", "anything") is None

    def test_empty_both_returns_none(self):
        assert cer("", "") is None

    def test_empty_hypothesis_all_deletions(self):
        # Deleting all 3 chars from "cat" / 3 reference chars → 1.0
        assert cer("cat", "") == pytest.approx(3 / 3)

    def test_single_substitution(self):
        # "cat" → "bat": 1 substitution / 3 reference chars
        assert cer("cat", "bat") == pytest.approx(1 / 3)

    def test_single_insertion(self):
        # "cat" → "cart": 1 insertion / 3 reference chars
        assert cer("cat", "cart") == pytest.approx(1 / 3)

    def test_single_deletion(self):
        # "cart" → "cat": 1 deletion / 4 reference chars
        assert cer("cart", "cat") == pytest.approx(1 / 4)

    def test_all_wrong_characters(self):
        # "abc" → "xyz": 3 substitutions / 3 chars → 1.0
        assert cer("abc", "xyz") == pytest.approx(1.0)

    def test_non_negative(self):
        assert cer("hello", "world") >= 0.0

    def test_unicode_characters(self):
        # Unicode emoji / multi-byte code points treated as single characters
        result = cer("héllo", "héllo")
        assert result == 0.0

    def test_unicode_substitution(self):
        # One Unicode substitution: "café" → "cafe" (é→e)
        result = cer("café", "cafe")
        assert result == pytest.approx(1 / 4)

    def test_whitespace_only_reference_has_chars(self):
        # "   " has 3 characters (spaces) → distance from "hello"
        result = cer("   ", "hello")
        assert result is not None
        assert result >= 0.0

    def test_hypothesis_longer_than_reference(self):
        # Extra insertions don't make ratio negative
        result = cer("hi", "hello world")
        assert result is not None
        assert result >= 0.0

    def test_long_string_non_negative(self):
        long_ref = "the quick brown fox jumps over the lazy dog " * 50
        long_hyp = "teh quikc brwon fox jmups over the lzy dog " * 50
        result = cer(long_ref, long_hyp)
        assert result is not None
        assert result >= 0.0

    def test_newlines_and_tabs_in_reference(self):
        # Newlines and tabs count as characters
        result = cer("a\nb", "a\nb")
        assert result == 0.0


# ---------------------------------------------------------------------------
# WER unit tests
# ---------------------------------------------------------------------------


class TestWER:
    """Unit tests for the wer() function."""

    def test_identical_strings_returns_zero(self):
        assert wer("hello world", "hello world") == 0.0

    def test_empty_reference_returns_none(self):
        assert wer("", "anything") is None

    def test_empty_both_returns_none(self):
        assert wer("", "") is None

    def test_whitespace_only_reference_returns_none(self):
        # Only whitespace → no words → unavailable (Req 7.6)
        assert wer("   ", "hello") is None
        assert wer("\t\n", "test") is None

    def test_empty_hypothesis_all_deletions(self):
        # "hello world" has 2 words; deleting both → 2/2 = 1.0
        assert wer("hello world", "") == pytest.approx(2 / 2)

    def test_single_substitution(self):
        # "hello world" → "hello earth": 1 substitution / 2 words
        assert wer("hello world", "hello earth") == pytest.approx(1 / 2)

    def test_single_insertion(self):
        # "hello world" → "hello beautiful world": 1 insertion / 2 ref words
        assert wer("hello world", "hello beautiful world") == pytest.approx(1 / 2)

    def test_single_deletion(self):
        # "hello beautiful world" → "hello world": 1 deletion / 3 ref words
        assert wer("hello beautiful world", "hello world") == pytest.approx(1 / 3)

    def test_all_wrong_words(self):
        # "one two three" → "a b c": 3 substitutions / 3 words = 1.0
        assert wer("one two three", "a b c") == pytest.approx(1.0)

    def test_non_negative(self):
        assert wer("foo bar", "baz qux quux") >= 0.0

    def test_unicode_words(self):
        result = wer("café résumé", "café résumé")
        assert result == 0.0

    def test_unicode_substitution(self):
        # "café naïve" → "cafe naive": 2 substitutions / 2 words = 1.0
        result = wer("café naïve", "cafe naive")
        assert result == pytest.approx(1.0)

    def test_extra_whitespace_ignored(self):
        # Leading/trailing/duplicate spaces don't create extra words
        assert wer("  hello   world  ", "hello world") == 0.0

    def test_single_word_reference(self):
        # "hello" → "world": 1 substitution / 1 word = 1.0
        assert wer("hello", "world") == pytest.approx(1.0)

    def test_hypothesis_longer_than_reference(self):
        result = wer("hi", "hello world how are you")
        assert result is not None
        assert result >= 0.0

    def test_long_string_non_negative(self):
        long_ref = "the quick brown fox jumps over the lazy dog " * 50
        long_hyp = "teh quikc brwon fox jmups over the lzy dog " * 50
        result = wer(long_ref, long_hyp)
        assert result is not None
        assert result >= 0.0

    def test_multiline_text(self):
        # Newlines are whitespace; words split across lines
        result = wer("hello\nworld", "hello\nworld")
        assert result == 0.0


# ---------------------------------------------------------------------------
# BeforeAfterReport and compute_before_after tests
# ---------------------------------------------------------------------------
"""Tests for BeforeAfterReport, compute_before_after, and store_evaluation_result.

Validates: Requirements 7.1, 7.2, 7.3, 7.6, 7.7
"""

import uuid

import pytest

from app.services.evaluation_service import BeforeAfterReport, compute_before_after


# Simple fake recognisers for deterministic tests
def _perfect_recogniser(crop_bytes: bytes) -> str:
    """Return the bytes decoded as UTF-8 — simulates perfect recognition."""
    return crop_bytes.decode("utf-8")


def _wrong_recogniser(crop_bytes: bytes) -> str:
    """Always returns a fixed wrong answer."""
    return "WRONG"


def _empty_recogniser(crop_bytes: bytes) -> str:
    """Always returns an empty string."""
    return ""


def _failing_recogniser(crop_bytes: bytes) -> str:
    """Always raises an exception — simulates engine failure."""
    raise RuntimeError("engine failure")


def _make_pair(reference: str) -> tuple[bytes, str]:
    """Build a held-out pair whose crop bytes encode the reference text."""
    return (reference.encode("utf-8"), reference)


class TestBeforeAfterReport:
    """Unit tests for BeforeAfterReport dataclass construction."""

    def test_fields_are_accessible(self):
        r = BeforeAfterReport(
            before_cer=0.1,
            before_wer=0.2,
            after_cer=0.05,
            after_wer=0.1,
            delta_cer=0.05,
            delta_wer=0.1,
            availability="available",
        )
        assert r.before_cer == pytest.approx(0.1)
        assert r.before_wer == pytest.approx(0.2)
        assert r.after_cer == pytest.approx(0.05)
        assert r.after_wer == pytest.approx(0.1)
        assert r.delta_cer == pytest.approx(0.05)
        assert r.delta_wer == pytest.approx(0.1)
        assert r.availability == "available"

    def test_none_fields_allowed(self):
        r = BeforeAfterReport(
            before_cer=None,
            before_wer=None,
            after_cer=None,
            after_wer=None,
            delta_cer=None,
            delta_wer=None,
            availability="unavailable",
        )
        assert r.before_cer is None
        assert r.availability == "unavailable"


class TestComputeBeforeAfterEmptySet:
    """compute_before_after with an empty held-out set (Req 7.6)."""

    def test_empty_held_out_returns_all_none(self):
        report = compute_before_after([], _perfect_recogniser, _perfect_recogniser)
        assert report.before_cer is None
        assert report.before_wer is None
        assert report.after_cer is None
        assert report.after_wer is None
        assert report.delta_cer is None
        assert report.delta_wer is None
        assert report.availability == "unavailable"


class TestComputeBeforeAfterPerfectRecognition:
    """compute_before_after when both models recognise perfectly."""

    def test_perfect_before_and_after(self):
        pairs = [_make_pair("hello world"), _make_pair("quick brown fox")]
        report = compute_before_after(pairs, _perfect_recogniser, _perfect_recogniser)
        assert report.before_cer == pytest.approx(0.0)
        assert report.before_wer == pytest.approx(0.0)
        assert report.after_cer == pytest.approx(0.0)
        assert report.after_wer == pytest.approx(0.0)
        assert report.delta_cer == pytest.approx(0.0)
        assert report.delta_wer == pytest.approx(0.0)
        assert report.availability == "available"


class TestComputeBeforeAfterImprovement:
    """compute_before_after when adapter is better than base model."""

    def test_improvement_gives_positive_delta(self):
        # base model is wrong, adapter is perfect
        pairs = [_make_pair("hello world")]
        report = compute_before_after(pairs, _wrong_recogniser, _perfect_recogniser)
        # before > after → positive delta
        assert report.before_cer is not None and report.before_cer > 0
        assert report.after_cer == pytest.approx(0.0)
        assert report.delta_cer is not None and report.delta_cer > 0

    def test_delta_equals_before_minus_after(self):
        pairs = [_make_pair("the cat sat"), _make_pair("on the mat")]
        report = compute_before_after(pairs, _wrong_recogniser, _perfect_recogniser)
        if report.before_cer is not None and report.after_cer is not None:
            assert report.delta_cer == pytest.approx(report.before_cer - report.after_cer)
        if report.before_wer is not None and report.after_wer is not None:
            assert report.delta_wer == pytest.approx(report.before_wer - report.after_wer)


class TestComputeBeforeAfterRegression:
    """compute_before_after when adapter is worse (regression)."""

    def test_regression_gives_negative_delta(self):
        # adapter wrong, base perfect
        pairs = [_make_pair("hello world")]
        report = compute_before_after(pairs, _perfect_recogniser, _wrong_recogniser)
        assert report.after_cer is not None and report.after_cer > 0
        assert report.before_cer == pytest.approx(0.0)
        assert report.delta_cer is not None and report.delta_cer < 0


class TestComputeBeforeAfterPartialFailure:
    """compute_before_after when a recogniser raises (Req 7.7)."""

    def test_failing_base_recogniser_treated_as_empty_hypothesis(self):
        # Base recogniser fails; adapter works perfectly.
        # The failing recogniser should not crash compute_before_after.
        # The failing pairs produce empty hypothesis → some error rate, not None.
        pairs = [_make_pair("hello world")]
        report = compute_before_after(pairs, _failing_recogniser, _perfect_recogniser)
        # before_cer should be non-None (we had a reference, just an empty hyp)
        assert report.before_cer is not None
        assert report.before_cer > 0.0
        # after should be perfect
        assert report.after_cer == pytest.approx(0.0)

    def test_failing_adapter_recogniser_treated_as_empty_hypothesis(self):
        pairs = [_make_pair("hello world")]
        report = compute_before_after(pairs, _perfect_recogniser, _failing_recogniser)
        assert report.before_cer == pytest.approx(0.0)
        assert report.after_cer is not None
        assert report.after_cer > 0.0

    def test_both_fail_but_reference_still_available(self):
        # Both fail → both produce empty hyp → CER/WER computed against reference
        pairs = [_make_pair("hello world")]
        report = compute_before_after(pairs, _failing_recogniser, _failing_recogniser)
        # Measurements are available (we have references), just bad
        assert report.before_cer is not None
        assert report.after_cer is not None
        assert report.availability == "available"


class TestComputeBeforeAfterMeasurementLevelFailure:
    """Req 7.7: metric-level failures retain sibling measurements.

    These tests patch ``cer`` or ``wer`` inside ``evaluation_service`` to raise
    an unexpected exception, verifying that the other three measurements are
    still computed and reported rather than all being suppressed.

    Validates: Requirements 7.7
    """

    def test_cer_failure_retains_wer_for_before_side(self, monkeypatch):
        """When before-CER computation raises, before-WER is still reported."""
        import app.services.evaluation_service as svc

        original_cer = svc.cer

        call_count = {"n": 0}

        def patched_cer(reference, hypothesis):
            call_count["n"] += 1
            # Only fail the first call (before-CER from base recogniser)
            if call_count["n"] == 1:
                raise RuntimeError("simulated CER failure")
            return original_cer(reference, hypothesis)

        monkeypatch.setattr(svc, "cer", patched_cer)

        pairs = [_make_pair("hello world")]
        report = svc.compute_before_after(pairs, _perfect_recogniser, _perfect_recogniser)

        # before_cer failed → None, but before_wer should still be available
        assert report.before_cer is None
        assert report.before_wer is not None
        # after measurements were computed with no failure
        assert report.after_cer is not None
        assert report.after_wer is not None

    def test_wer_failure_retains_cer_for_after_side(self, monkeypatch):
        """When after-WER computation raises, after-CER is still reported."""
        import app.services.evaluation_service as svc

        original_wer = svc.wer

        call_count = {"n": 0}

        def patched_wer(reference, hypothesis):
            call_count["n"] += 1
            # Fail the second call (after-WER from adapter recogniser)
            if call_count["n"] == 2:
                raise RuntimeError("simulated WER failure")
            return original_wer(reference, hypothesis)

        monkeypatch.setattr(svc, "wer", patched_wer)

        pairs = [_make_pair("hello world")]
        report = svc.compute_before_after(pairs, _perfect_recogniser, _perfect_recogniser)

        # after_wer failed → None, but after_cer should still be available
        assert report.after_wer is None
        assert report.after_cer is not None
        # before measurements were computed with no failure
        assert report.before_cer is not None
        assert report.before_wer is not None

    def test_failed_measurement_is_none_successful_measurement_is_not_none(self, monkeypatch):
        """Mixed availability: one None field does not make all fields None."""
        import app.services.evaluation_service as svc

        original_cer = svc.cer
        called = {"done": False}

        def patched_cer(reference, hypothesis):
            if not called["done"]:
                called["done"] = True
                raise ValueError("forced metric error")
            return original_cer(reference, hypothesis)

        monkeypatch.setattr(svc, "cer", patched_cer)

        pairs = [_make_pair("the quick brown fox")]
        report = svc.compute_before_after(pairs, _perfect_recogniser, _perfect_recogniser)

        # Exactly one CER measurement failed; others retained
        none_count = sum(
            1
            for v in (report.before_cer, report.before_wer, report.after_cer, report.after_wer)
            if v is None
        )
        non_none_count = sum(
            1
            for v in (report.before_cer, report.before_wer, report.after_cer, report.after_wer)
            if v is not None
        )
        assert none_count >= 1, "At least one measurement should be None (the failing one)"
        assert non_none_count >= 1, "At least one measurement should be retained"

    def test_availability_unavailable_when_any_measurement_missing(self, monkeypatch):
        """Availability is 'unavailable' when any measurement is None."""
        import app.services.evaluation_service as svc

        original_cer = svc.cer
        called = {"done": False}

        def patched_cer(reference, hypothesis):
            if not called["done"]:
                called["done"] = True
                raise RuntimeError("CER forced failure")
            return original_cer(reference, hypothesis)

        monkeypatch.setattr(svc, "cer", patched_cer)

        pairs = [_make_pair("hello world")]
        report = svc.compute_before_after(pairs, _perfect_recogniser, _perfect_recogniser)

        # If any measurement is None, availability must be "unavailable"
        has_none = any(
            v is None
            for v in (report.before_cer, report.before_wer, report.after_cer, report.after_wer)
        )
        if has_none:
            assert report.availability == "unavailable"

    def test_delta_is_none_when_one_side_missing(self, monkeypatch):
        """Delta is None when either before or after is None (Req 7.3)."""
        import app.services.evaluation_service as svc

        original_cer = svc.cer
        called = {"done": False}

        def patched_cer(reference, hypothesis):
            if not called["done"]:
                called["done"] = True
                raise RuntimeError("CER forced failure")
            return original_cer(reference, hypothesis)

        monkeypatch.setattr(svc, "cer", patched_cer)

        pairs = [_make_pair("hello world")]
        report = svc.compute_before_after(pairs, _perfect_recogniser, _perfect_recogniser)

        # The patched CER fails only on first call (before_cer → None)
        # before_cer is None → delta_cer must be None
        assert report.before_cer is None
        assert report.delta_cer is None


class TestComputeBeforeAfterAvailability:
    """Availability flag semantics (Req 7.6)."""

    def test_non_empty_set_with_valid_references_is_available(self):
        pairs = [_make_pair("test sentence")]
        report = compute_before_after(pairs, _perfect_recogniser, _perfect_recogniser)
        assert report.availability == "available"

    def test_empty_set_is_unavailable(self):
        report = compute_before_after([], _perfect_recogniser, _perfect_recogniser)
        assert report.availability == "unavailable"

    def test_whitespace_only_reference_is_unavailable(self):
        # The reference has chars but no words; CER is defined but WER is None.
        # Since not all four measurements are present, availability → "unavailable".
        pairs = [(b"   ", "   ")]
        report = compute_before_after(pairs, lambda b: b.decode(), lambda b: b.decode())
        # WER is None because whitespace-only reference has no words
        assert report.before_wer is None
        assert report.after_wer is None
        assert report.delta_wer is None
        assert report.availability == "unavailable"


class TestComputeBeforeAfterDeltaArithmetic:
    """Delta is always before − after (Req 7.3)."""

    def test_delta_cer_matches_before_minus_after(self):
        pairs = [_make_pair("one two three")]
        report = compute_before_after(pairs, _wrong_recogniser, _empty_recogniser)
        if report.before_cer is not None and report.after_cer is not None:
            expected = report.before_cer - report.after_cer
            assert report.delta_cer == pytest.approx(expected)

    def test_delta_is_none_when_before_is_none(self):
        # Empty reference → before is None → delta must be None
        pairs = [(b"", "")]
        report = compute_before_after(pairs, lambda b: "", lambda b: "")
        assert report.before_cer is None
        assert report.delta_cer is None

    def test_delta_is_none_when_after_is_none(self):
        # Make after unavailable by giving the after-recogniser a whitespace ref
        # Use an all-whitespace reference so WER is None.
        pairs = [(b"   ", "   ")]
        report = compute_before_after(pairs, lambda b: "   ", lambda b: "   ")
        assert report.delta_wer is None


class TestComputeBeforeAfterMultiplePairs:
    """Aggregate CER/WER over multiple pairs (concatenated reference)."""

    def test_multiple_pairs_aggregate(self):
        pairs = [_make_pair("hello"), _make_pair("world")]
        report = compute_before_after(pairs, _perfect_recogniser, _perfect_recogniser)
        assert report.before_cer == pytest.approx(0.0)
        assert report.after_cer == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# store_evaluation_result tests (with in-memory SQLite)
# ---------------------------------------------------------------------------


class TestStoreEvaluationResult:
    """Tests for store_evaluation_result (Req 7.3)."""

    @pytest.fixture()
    def db_session(self):
        from app.data import Base, create_all, create_db_engine, create_session_factory

        engine = create_db_engine("sqlite://")
        create_all(engine)
        factory = create_session_factory(engine)
        with factory() as session:
            yield session
        Base.metadata.drop_all(engine)

    @pytest.fixture()
    def repos(self, db_session):
        from app.data import RepositoryFactory

        return RepositoryFactory(db_session)

    @pytest.fixture()
    def user_and_run(self, db_session, repos):
        from app.data import TrainingRun, TrainingRunState, User

        user = User(username="eva", email="eva@example.com", password_hash="h")
        db_session.add(user)
        db_session.flush()
        run = TrainingRun(user_id=user.user_id, state=TrainingRunState.ACCEPTED)
        db_session.add(run)
        db_session.flush()
        return user, run

    def test_persists_full_report(self, repos, db_session, user_and_run):
        from app.services.evaluation_service import store_evaluation_result

        user, run = user_and_run
        report = BeforeAfterReport(
            before_cer=0.3,
            before_wer=0.5,
            after_cer=0.1,
            after_wer=0.2,
            delta_cer=0.2,
            delta_wer=0.3,
            availability="available",
        )
        result = store_evaluation_result(report, run.run_id, user.user_id, repos)
        db_session.commit()

        fetched = repos.evaluation_results.get_for_run(user.user_id, run.run_id)
        assert fetched is not None
        assert fetched.before_cer == pytest.approx(0.3)
        assert fetched.before_wer == pytest.approx(0.5)
        assert fetched.after_cer == pytest.approx(0.1)
        assert fetched.after_wer == pytest.approx(0.2)
        assert fetched.delta_cer == pytest.approx(0.2)
        assert fetched.delta_wer == pytest.approx(0.3)
        from app.data.enums import EvaluationAvailability
        assert fetched.availability == EvaluationAvailability.AVAILABLE

    def test_persists_unavailable_report(self, repos, db_session, user_and_run):
        from app.services.evaluation_service import store_evaluation_result

        user, run = user_and_run
        report = BeforeAfterReport(
            before_cer=None,
            before_wer=None,
            after_cer=None,
            after_wer=None,
            delta_cer=None,
            delta_wer=None,
            availability="unavailable",
        )
        store_evaluation_result(report, run.run_id, user.user_id, repos)
        db_session.commit()

        fetched = repos.evaluation_results.get_for_run(user.user_id, run.run_id)
        assert fetched is not None
        assert fetched.before_cer is None
        assert fetched.after_cer is None
        from app.data.enums import EvaluationAvailability
        assert fetched.availability == EvaluationAvailability.UNAVAILABLE

    def test_persists_partial_report_with_nones(self, repos, db_session, user_and_run):
        from app.services.evaluation_service import store_evaluation_result

        user, run = user_and_run
        report = BeforeAfterReport(
            before_cer=0.4,
            before_wer=None,  # whitespace-only reference → no words
            after_cer=0.2,
            after_wer=None,
            delta_cer=0.2,
            delta_wer=None,
            availability="unavailable",
        )
        store_evaluation_result(report, run.run_id, user.user_id, repos)
        db_session.commit()

        fetched = repos.evaluation_results.get_for_run(user.user_id, run.run_id)
        assert fetched is not None
        assert fetched.before_cer == pytest.approx(0.4)
        assert fetched.before_wer is None
        assert fetched.delta_wer is None

    def test_result_is_scoped_to_user(self, repos, db_session, user_and_run):
        from app.data import TrainingRun, TrainingRunState, User
        from app.services.evaluation_service import store_evaluation_result

        user, run = user_and_run
        other_user = User(username="other", email="other@example.com", password_hash="h")
        db_session.add(other_user)
        db_session.flush()

        report = BeforeAfterReport(
            before_cer=0.1,
            before_wer=0.1,
            after_cer=0.0,
            after_wer=0.0,
            delta_cer=0.1,
            delta_wer=0.1,
            availability="available",
        )
        store_evaluation_result(report, run.run_id, user.user_id, repos)
        db_session.commit()

        # Other user cannot access this result
        other_results = repos.evaluation_results.list_for_user(other_user.user_id)
        assert len(other_results) == 0
