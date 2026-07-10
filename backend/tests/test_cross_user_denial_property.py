"""Property test for cross-user access denial without disclosure (task 3.4).

Design property under test:

    Property 28: Cross-user access is denied without disclosure
    *For any* pair of distinct users A and B, a request by B to access A's
    training pairs, recognized text, or adapter is denied and returns none of
    A's data.

**Validates: Requirements 10.5**

This is a single Hypothesis property test (the project's ``default`` profile runs
>= 100 iterations, see ``tests/conftest.py`` and ``PROPERTY_TESTING.md``). It
stands up a small FastAPI app whose owned-resource endpoints funnel per-user
scoped repository lookups through the real auth gate
(:func:`app.services.auth_api.get_current_user`) and the isolation helpers
(:mod:`app.services.isolation`), exactly as production code is expected to.

For each generated example we:

* create a resource (a training pair, a recognized word, or an adapter) carrying
  a distinctive secret, owned by one of two distinct users (owner);
* confirm the owner can read it (200) so the resource genuinely exists, making
  the cross-user denial a real isolation decision rather than coincidental
  absence;
* have the *other* (non-owner) user attempt to access it and assert the response
  is always **404** (never 403, never 200), carries only the generic
  not-found message, discloses none of the resource's secret contents, and is
  byte-for-byte indistinguishable from the response for a genuinely missing
  resource.

To keep 100+ iterations cheap, the two users are created and authenticated once
in a fixture (PBKDF2 password hashing is expensive); each example only inserts
and then deletes its own resource.
"""

from __future__ import annotations

import uuid
from typing import Iterator

import pytest
from fastapi import Depends, FastAPI, status
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.data import (
    Adapter,
    AdapterStatus,
    Base,
    Image,
    ImageFormat,
    ImageStatus,
    RecognizedWord,
    RepositoryFactory,
    TrainingPair,
    TrainingPairOrigin,
    User,
    create_all,
    create_db_engine,
    create_session_factory,
)
from app.services import auth_api
from app.services.auth import SessionStore, hash_password
from app.services.auth_api import (
    get_current_user,
    get_db_session,
    get_session_store,
    router as auth_router,
)
from app.services.isolation import (
    RESOURCE_NOT_FOUND_MESSAGE,
    get_or_404,
    register_isolation_handlers,
    require_owned,
)

RESOURCE_KINDS = ("training_pair", "recognized_word", "adapter")

# Distinctive secrets are prefixed with this marker and drawn from an alphabet
# with no spaces, so a generated secret can never be a substring of the generic
# "Resource not found" message. That keeps the "secret not disclosed" assertion
# free of false negatives while still constraining to the meaningful input space.
_SECRET_PREFIX = "secret-"
_SECRET_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
)


# --------------------------------------------------------------------------- #
# Generators
# --------------------------------------------------------------------------- #
secrets_strategy = st.builds(
    lambda body: _SECRET_PREFIX + body,
    st.text(alphabet=_SECRET_ALPHABET, min_size=1, max_size=40),
)

coords_strategy = st.integers(min_value=0, max_value=10_000)

scenarios = st.fixed_dictionaries(
    {
        "kind": st.sampled_from(RESOURCE_KINDS),
        "secret": secrets_strategy,
        # Vary which of the two distinct users owns the resource (and therefore
        # which one is the non-owner attempting access).
        "owner_is_alice": st.booleans(),
        "region": st.fixed_dictionaries(
            {
                "x": coords_strategy,
                "y": coords_strategy,
                "w": st.integers(min_value=1, max_value=10_000),
                "h": st.integers(min_value=1, max_value=10_000),
            }
        ),
    }
)


# --------------------------------------------------------------------------- #
# App + two authenticated, distinct users (built once, reused across examples)
# --------------------------------------------------------------------------- #
@pytest.fixture()
def isolation_client():
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    session = factory()
    store = SessionStore(session)

    # Two distinct users, created and authenticated once.
    with factory() as s:
        alice = User(
            username="alice",
            email="alice@example.com",
            password_hash=hash_password("pw"),
        )
        bob = User(
            username="bob",
            email="bob@example.com",
            password_hash=hash_password("pw"),
        )
        s.add_all([alice, bob])
        s.commit()
        alice_id = alice.user_id
        bob_id = bob.user_id

    def _override_db_session() -> Iterator[Session]:
        with factory() as s:
            yield s

    def _override_store() -> SessionStore:
        return store

    app = FastAPI()
    app.include_router(auth_router)
    register_isolation_handlers(app)

    @app.get("/training-pairs/{pair_id}")
    def get_training_pair(
        pair_id: uuid.UUID,
        user: User = Depends(get_current_user),
        repos: RepositoryFactory = Depends(auth_api.get_repositories),
    ) -> dict:
        pair = require_owned(repos.training_pairs.get(user.user_id, pair_id))
        return {"pair_id": str(pair.pair_id), "text": pair.corrected_text}

    @app.get("/recognized-words/{word_id}")
    def get_recognized_word(
        word_id: uuid.UUID,
        user: User = Depends(get_current_user),
        repos: RepositoryFactory = Depends(auth_api.get_repositories),
    ) -> dict:
        word = get_or_404(repos.recognized_words.get(user.user_id, word_id))
        return {"word_id": str(word.word_id), "text": word.text}

    @app.get("/adapters/{adapter_id}")
    def get_adapter(
        adapter_id: uuid.UUID,
        user: User = Depends(get_current_user),
        repos: RepositoryFactory = Depends(auth_api.get_repositories),
    ) -> dict:
        adapter = require_owned(repos.adapters.get_for_user(user.user_id))
        # Even when the user *has* an adapter, a request for someone else's
        # adapter id must not return it.
        if adapter.adapter_id != adapter_id:
            require_owned(None)
        return {"adapter_id": str(adapter.adapter_id), "weights": adapter.weights_path}

    app.dependency_overrides[get_db_session] = _override_db_session
    app.dependency_overrides[get_session_store] = _override_store

    with TestClient(app) as client:
        alice_token = client.post(
            "/auth/login", json={"username": "alice", "password": "pw"}
        ).json()["access_token"]
        bob_token = client.post(
            "/auth/login", json={"username": "bob", "password": "pw"}
        ).json()["access_token"]
        yield {
            "client": client,
            "factory": factory,
            "alice_id": alice_id,
            "bob_id": bob_id,
            "alice_token": alice_token,
            "bob_token": bob_token,
        }
    Base.metadata.drop_all(engine)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_resource(factory, owner_id, scenario):
    """Insert the scenario's resource owned by ``owner_id``; return (path, cleanup)."""
    kind = scenario["kind"]
    secret = scenario["secret"]
    region = scenario["region"]
    with factory() as s:
        repos = RepositoryFactory(s)
        if kind == "training_pair":
            pair = repos.training_pairs.add(
                TrainingPair(
                    user_id=owner_id,
                    source_region_key="rk",
                    region_crop_path="crop.png",
                    corrected_text=secret,
                    origin=TrainingPairOrigin.CORRECTION,
                )
            )
            s.commit()
            path = f"/training-pairs/{pair.pair_id}"
        elif kind == "recognized_word":
            image = repos.images.add(
                Image(
                    user_id=owner_id,
                    storage_path="img.png",
                    format=ImageFormat.PNG,
                    size_bytes=10,
                    status=ImageStatus.RECOGNIZED,
                )
            )
            s.commit()
            word = repos.recognized_words.add(
                RecognizedWord(
                    image_id=image.image_id,
                    line_index=0,
                    word_index=0,
                    text=secret,
                    region_x=region["x"],
                    region_y=region["y"],
                    region_width=region["w"],
                    region_height=region["h"],
                )
            )
            s.commit()
            path = f"/recognized-words/{word.word_id}"
        else:  # adapter
            adapter = repos.adapters.add(
                Adapter(
                    user_id=owner_id,
                    weights_path=secret,
                    base_model_version="base-v1",
                    status=AdapterStatus.ACTIVE,
                )
            )
            s.commit()
            path = f"/adapters/{adapter.adapter_id}"

    def cleanup() -> None:
        with factory() as s:
            repos = RepositoryFactory(s)
            if kind == "training_pair":
                pair_id = uuid.UUID(path.rsplit("/", 1)[1])
                repos.training_pairs.delete(owner_id, pair_id)
            elif kind == "recognized_word":
                # Remove every owned word + image so each example starts clean.
                for img in list(repos.images.list_for_user(owner_id)):
                    for w in list(
                        repos.recognized_words.list_for_image(owner_id, img.image_id)
                    ):
                        s.delete(w)
                    s.delete(img)
            else:
                repos.adapters.delete_for_user(owner_id)
            s.commit()

    return path, cleanup


def _missing_path(kind: str) -> str:
    """A path of the same shape pointing at a wholly nonexistent resource."""
    if kind == "training_pair":
        return f"/training-pairs/{uuid.uuid4()}"
    if kind == "recognized_word":
        return f"/recognized-words/{uuid.uuid4()}"
    return f"/adapters/{uuid.uuid4()}"


# Feature: handwriting-notes-digitizer, Property 28: Cross-user access is denied without disclosure
@given(scenario=scenarios)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_cross_user_access_is_denied_without_disclosure(
    isolation_client, scenario
) -> None:
    """For any distinct owner/non-owner pair, the non-owner's access to the
    owner's training pair, recognized text, or adapter is denied with a 404
    (never 403/200), discloses none of the resource's data, and is
    indistinguishable from a genuinely missing resource.

    **Validates: Requirements 10.5**
    """
    ctx = isolation_client
    factory = ctx["factory"]
    client = ctx["client"]

    if scenario["owner_is_alice"]:
        owner_id, owner_token, intruder_token = (
            ctx["alice_id"],
            ctx["alice_token"],
            ctx["bob_token"],
        )
    else:
        owner_id, owner_token, intruder_token = (
            ctx["bob_id"],
            ctx["bob_token"],
            ctx["alice_token"],
        )

    path, cleanup = _create_resource(factory, owner_id, scenario)
    try:
        # The resource genuinely exists: the owner can read it (200). This makes
        # the cross-user denial a real isolation decision, not mere absence.
        owner_resp = client.get(path, headers=_auth(owner_token))
        assert owner_resp.status_code == status.HTTP_200_OK

        # The non-owner is denied.
        intruder_resp = client.get(path, headers=_auth(intruder_token))

        # 404 specifically -- never 200 (would leak data) and never 403 (would
        # leak existence).
        assert intruder_resp.status_code == status.HTTP_404_NOT_FOUND
        assert intruder_resp.status_code != status.HTTP_403_FORBIDDEN
        assert intruder_resp.status_code != status.HTTP_200_OK

        # The body carries only the generic not-found message and none of the
        # owner's secret data.
        assert intruder_resp.json() == {"detail": RESOURCE_NOT_FOUND_MESSAGE}
        assert scenario["secret"] not in intruder_resp.text

        # Indistinguishable from a request for a genuinely nonexistent resource:
        # same status code and identical body.
        missing_resp = client.get(
            _missing_path(scenario["kind"]), headers=_auth(intruder_token)
        )
        assert missing_resp.status_code == intruder_resp.status_code
        assert missing_resp.json() == intruder_resp.json()

        # The owner's data is untouched by the denied access.
        owner_again = client.get(path, headers=_auth(owner_token))
        assert owner_again.status_code == status.HTTP_200_OK
        assert owner_again.json() == owner_resp.json()
    finally:
        cleanup()
