"""Unit coverage for durable recognition-job delivery and recovery semantics."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.data import Base, create_all, create_db_engine, create_session_factory
from app.data.enums import RecognitionJobState
from app.data.models import RecognitionJob, User
from app.data.base import utcnow
from app.services import jobs


@pytest.fixture()
def session_factory():
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    yield factory
    Base.metadata.drop_all(engine)


def _add_user(session: Session) -> User:
    user = User(
        username=f"jobs-{uuid.uuid4().hex}",
        email=f"{uuid.uuid4().hex}@example.com",
        password_hash="test-only",
    )
    session.add(user)
    session.commit()
    return user


def _add_job(
    session: Session,
    user: User,
    *,
    state: RecognitionJobState = RecognitionJobState.QUEUED,
    attempt: int = 0,
    max_attempts: int = 2,
    deadline_at: dt.datetime | None = None,
) -> RecognitionJob:
    job = RecognitionJob(
        user_id=user.user_id,
        state=state,
        attempt=attempt,
        max_attempts=max_attempts,
        deadline_at=deadline_at,
        region_ids=[str(uuid.uuid4())],
    )
    session.add(job)
    session.commit()
    return job


def test_enqueue_deduplicates_duplicate_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    submitted: list[uuid.UUID] = []

    class FakeExecutor:
        def submit(self, function, job_id, session_factory, store):
            submitted.append(job_id)
            return None

    monkeypatch.setattr(jobs, "_executor", FakeExecutor())
    monkeypatch.setattr(jobs, "_submitted", set())
    job_id = uuid.uuid4()

    jobs._enqueue(job_id, object(), object())
    jobs._enqueue(job_id, object(), object())

    assert submitted == [job_id]


def test_retry_is_bounded_and_terminal_state_is_persisted(db_session: Session) -> None:
    user = _add_user(db_session)
    retry_job = _add_job(db_session, user, attempt=1, max_attempts=2)

    assert jobs._queue_retry(db_session, retry_job, "temporary failure") is True
    assert retry_job.state is RecognitionJobState.QUEUED
    assert retry_job.deadline_at is None
    assert retry_job.error_message == "temporary failure"

    terminal_job = _add_job(db_session, user, attempt=2, max_attempts=2)
    assert jobs._queue_retry(db_session, terminal_job, "permanent failure") is False
    assert terminal_job.state is RecognitionJobState.FAILED
    assert terminal_job.error_message == "permanent failure"


def test_worker_crash_requeues_claimed_job(monkeypatch: pytest.MonkeyPatch, session_factory) -> None:
    with session_factory() as session:
        user = _add_user(session)
        job = _add_job(session, user)
        job_id = job.job_id

    def crash_on_refresh(self, instance, attribute_names=None, with_for_update=None):
        raise RuntimeError("simulated worker crash")

    resubmitted: list[uuid.UUID] = []
    monkeypatch.setattr(Session, "refresh", crash_on_refresh)
    monkeypatch.setattr(
        jobs,
        "_enqueue",
        lambda job_id, session_factory, store: resubmitted.append(job_id),
    )

    jobs._run_job(job_id, session_factory, object())

    with session_factory() as session:
        recovered = session.get(RecognitionJob, job_id)
        assert recovered is not None
        assert recovered.state is RecognitionJobState.QUEUED
        assert recovered.error_message == "Recognition worker crashed."
    assert resubmitted == [job_id]


def test_deadline_timeout_is_terminal_after_last_attempt(
    monkeypatch: pytest.MonkeyPatch, session_factory
) -> None:
    with session_factory() as session:
        user = _add_user(session)
        job = _add_job(session, user, max_attempts=1)
        job_id = job.job_id

    monkeypatch.setattr(jobs, "_deadline_expired", lambda deadline: True)
    monkeypatch.setattr(jobs, "_enqueue", lambda *args: None)

    jobs._run_job(job_id, session_factory, object())

    with session_factory() as session:
        finished = session.get(RecognitionJob, job_id)
        assert finished is not None
        assert finished.state is RecognitionJobState.TIMEOUT
        assert finished.error_message == "Recognition job exceeded its deadline."


def test_stale_leases_are_requeued_or_timed_out(session_factory) -> None:
    stale_at = utcnow() - dt.timedelta(minutes=5)
    with session_factory() as session:
        user = _add_user(session)
        retry_job = _add_job(
            session,
            user,
            state=RecognitionJobState.RUNNING,
            attempt=1,
            max_attempts=2,
            deadline_at=stale_at,
        )
        terminal_job = _add_job(
            session,
            user,
            state=RecognitionJobState.RUNNING,
            attempt=2,
            max_attempts=2,
            deadline_at=stale_at,
        )
        retry_id = retry_job.job_id
        terminal_id = terminal_job.job_id

    assert jobs.recover_stale_jobs(session_factory) == 2

    with session_factory() as session:
        retry = session.get(RecognitionJob, retry_id)
        terminal = session.get(RecognitionJob, terminal_id)
        assert retry is not None and retry.state is RecognitionJobState.QUEUED
        assert terminal is not None and terminal.state is RecognitionJobState.TIMEOUT
        assert retry.deadline_at is None
