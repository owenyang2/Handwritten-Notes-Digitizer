"""Property-based test for the authentication gate (design Property 2).

This test exercises the Auth + Session layer's gate (``get_current_user`` in
``app.services.auth_api``) against the design's **Property 2**:

    For any image, correction, or calibration submission made without valid
    authentication, the request is rejected with an authentication-required
    indication and the persisted-data state is identical before and after the
    request.

**Validates: Requirements 1.5, 10.1, 10.4**

Strategy
--------
A tiny FastAPI app wires three *gated* submission endpoints that mirror the real
endpoints in the design's API Surface (``POST /images``,
``POST /images/{id}/corrections``, ``POST /calibration/{id}/samples``). Each
endpoint depends on the real ``get_current_user`` gate and, *if reached*, writes
a row to the database. Because the gate runs as a FastAPI dependency, it rejects
unauthenticated requests with ``401`` *before* any handler body executes, so a
rejected submission must store nothing.

Hypothesis generates arbitrary unauthenticated submissions: a randomized
endpoint (image / correction / calibration) with a randomized payload, paired
with a randomized credential that is never valid (missing header, empty header,
malformed scheme, random bearer token, or random session cookie). The session
store is empty -- we never log in -- so no generated token can resolve to a
user. For every generated request the test asserts the response is ``401`` with
the authentication-required message and that a full snapshot of persisted-data
row counts is byte-for-byte identical before and after the request.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Depends, FastAPI, Request, status
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.pool import StaticPool

from app.data import (
    Adapter,
    Base,
    EvaluationResult,
    Image,
    ImageFormat,
    ImageStatus,
    RecognizedWord,
    RepositoryFactory,
    TrainingPair,
    TrainingPairOrigin,
    TrainingRun,
    TrainingRunPair,
    User,
    create_all,
    create_db_engine,
    create_session_factory,
)
from app.services import auth_api
from app.services.auth import SessionStore
from app.services.auth_api import (
    AUTH_REQUIRED_MESSAGE,
    get_current_user,
    get_db_session,
    get_session_store,
    router as auth_router,
)

# Every persisted table; their combined row counts capture "persisted-data
# state" so we can assert it is unchanged across a rejected request.
_MODELS = [
    User,
    Image,
    RecognizedWord,
    TrainingPair,
    Adapter,
    TrainingRun,
    TrainingRunPair,
    EvaluationResult,
]


def _build_app() -> tuple[TestClient, Any]:
    """Build the gated test app over a fresh in-memory DB with seeded data.

    The seeded data (a user, an image, and a training pair) makes the
    "identical before/after" assertion meaningful: the state being preserved is
    non-empty. We insert a placeholder ``password_hash`` directly (no real
    hashing) because authentication is never attempted in this test.
    """
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    store = SessionStore()  # empty: no token will ever resolve to a user.

    # Seed baseline persisted state.
    with factory() as s:
        repos = RepositoryFactory(s)
        user = repos.users.add(
            User(username="seed", email="seed@example.com", password_hash="x")
        )
        s.flush()
        image = repos.images.add(
            Image(
                user_id=user.user_id,
                storage_path="images/seed.png",
                format=ImageFormat.PNG,
                size_bytes=10,
                status=ImageStatus.ACCEPTED,
            )
        )
        s.flush()
        repos.training_pairs.add(
            TrainingPair(
                user_id=user.user_id,
                image_id=image.image_id,
                source_region_key="seed-region",
                region_crop_path="crops/seed.png",
                corrected_text="seed",
                origin=TrainingPairOrigin.CORRECTION,
            )
        )
        s.commit()

    app = FastAPI()
    app.include_router(auth_router)

    # --- Gated endpoints mirroring the real submission surface. Each writes a
    # row if the gate lets the request through; an unauthenticated request must
    # never reach these bodies. ---

    @app.post("/images")
    async def submit_image(
        request: Request,
        user: User = Depends(get_current_user),
        repos: RepositoryFactory = Depends(auth_api.get_repositories),
    ) -> dict[str, str]:
        await request.body()
        image = repos.images.add(
            Image(
                user_id=user.user_id,
                storage_path="images/uploaded.png",
                format=ImageFormat.PNG,
                size_bytes=10,
                status=ImageStatus.ACCEPTED,
            )
        )
        repos.session.commit()
        return {"image_id": str(image.image_id)}

    @app.post("/images/{image_id}/corrections")
    async def submit_correction(
        image_id: uuid.UUID,
        request: Request,
        user: User = Depends(get_current_user),
        repos: RepositoryFactory = Depends(auth_api.get_repositories),
    ) -> dict[str, str]:
        await request.body()
        pair = repos.training_pairs.add(
            TrainingPair(
                user_id=user.user_id,
                source_region_key=f"correction-{uuid.uuid4()}",
                region_crop_path="crops/uploaded.png",
                corrected_text="corrected",
                origin=TrainingPairOrigin.CORRECTION,
            )
        )
        repos.session.commit()
        return {"pair_id": str(pair.pair_id)}

    @app.post("/calibration/{session_id}/samples")
    async def submit_calibration(
        session_id: uuid.UUID,
        request: Request,
        user: User = Depends(get_current_user),
        repos: RepositoryFactory = Depends(auth_api.get_repositories),
    ) -> dict[str, str]:
        await request.body()
        pair = repos.training_pairs.add(
            TrainingPair(
                user_id=user.user_id,
                source_region_key=f"calibration-{uuid.uuid4()}",
                region_crop_path="crops/calibration.png",
                corrected_text="prompt",
                origin=TrainingPairOrigin.CALIBRATION,
            )
        )
        repos.session.commit()
        return {"pair_id": str(pair.pair_id)}

    def _override_db_session():
        with factory() as s:
            yield s

    app.dependency_overrides[get_db_session] = _override_db_session
    app.dependency_overrides[get_session_store] = lambda: store

    return TestClient(app), factory


_CLIENT, _FACTORY = _build_app()


def _snapshot() -> dict[str, int]:
    """Row counts for every persisted table -> the persisted-data state."""
    with _FACTORY() as s:
        return {
            m.__name__: int(s.scalar(select(func.count()).select_from(m)) or 0)
            for m in _MODELS
        }


# Tokens/credential values: printable ASCII without spaces or control chars so
# they are valid header/cookie values. None of these resolve in the empty store.
_token = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126),
    min_size=0,
    max_size=64,
)


def _unauthenticated_request_kwargs() -> st.SearchStrategy[dict[str, Any]]:
    """Varied credentials that are all invalid/missing."""
    return st.one_of(
        st.just({}),  # no Authorization header, no cookie
        st.just({"headers": {"Authorization": ""}}),  # empty header
        st.just({"headers": {"Authorization": "Bearer "}}),  # empty bearer
        _token.map(lambda t: {"headers": {"Authorization": f"Bearer {t}"}}),
        _token.map(lambda t: {"headers": {"Authorization": f"Basic {t}"}}),
        _token.map(lambda t: {"headers": {"Authorization": t}}),  # no scheme
        # Session cookie sent via header so it does not persist on the shared
        # client across examples.
        _token.filter(bool).map(lambda t: {"headers": {"Cookie": f"session={t}"}}),
    )


def _submission() -> st.SearchStrategy[tuple[str, dict[str, Any]]]:
    """A gated submission endpoint with an arbitrary payload."""
    body = st.binary(min_size=0, max_size=128)
    ids = st.uuids().map(str)
    return st.one_of(
        body.map(lambda b: ("/images", {"content": b})),
        st.tuples(ids, body).map(
            lambda t: (f"/images/{t[0]}/corrections", {"content": t[1]})
        ),
        st.tuples(ids, body).map(
            lambda t: (f"/calibration/{t[0]}/samples", {"content": t[1]})
        ),
    )


# Feature: handwriting-notes-digitizer, Property 2: Authentication gate stores nothing on unauthenticated requests
@given(submission=_submission(), credential=_unauthenticated_request_kwargs())
def test_auth_gate_stores_nothing_on_unauthenticated_requests(
    submission: tuple[str, dict[str, Any]],
    credential: dict[str, Any],
) -> None:
    """Validates: Requirements 1.5, 10.1, 10.4.

    Any unauthenticated image/correction/calibration submission is rejected with
    an authentication-required indication, and the persisted-data state is
    identical before and after the request.
    """
    path, payload = submission

    before = _snapshot()

    resp = _CLIENT.post(path, **payload, **credential)

    # Rejected with an authentication-required indication (Req 10.4).
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED
    assert resp.json()["detail"] == AUTH_REQUIRED_MESSAGE

    # Persisted-data state is identical before and after (Req 1.5, 10.4).
    after = _snapshot()
    assert after == before
