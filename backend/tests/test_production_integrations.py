"""Opt-in contract tests for the production database and object store.

These tests intentionally do not create containers or infer credentials. They
run only when the caller supplies disposable integration endpoints, so a normal
unit-test run cannot mutate a developer's PostgreSQL or S3 account.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect

from app.data.migrations import upgrade_database
from app.data.s3_object_store import S3CompatibleObjectStore


def test_postgres_migrations_reach_the_current_schema() -> None:
    database_url = os.environ.get("HND_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("set HND_TEST_POSTGRES_URL to a disposable PostgreSQL database")
    pytest.importorskip("psycopg")

    upgrade_database(database_url)
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        expected = {"users", "user_sessions", "documents", "document_operations"}
        assert expected.issubset(tables)

        session_columns = {column["name"] for column in inspector.get_columns("user_sessions")}
        assert "token_hash" in session_columns
        assert "session_id" in session_columns
        assert "token" not in session_columns
        # Adding model provenance must preserve both existing review references.
        for table in ("region_reviews", "review_revisions"):
            references = inspector.get_foreign_keys(table)
            assert any(foreign_key["constrained_columns"] == ["prediction_id"]
                       and foreign_key["referred_table"] == "region_predictions"
                       and foreign_key["referred_columns"] == ["prediction_id"]
                       for foreign_key in references)
        assert any(foreign_key["constrained_columns"] == ["model_assignment_id"]
                   and foreign_key["referred_table"] == "model_assignments"
                   for foreign_key in inspector.get_foreign_keys("region_predictions"))
    finally:
        engine.dispose()


@pytest.fixture()
def s3_store() -> S3CompatibleObjectStore:
    endpoint = os.environ.get("HND_TEST_S3_ENDPOINT_URL")
    bucket = os.environ.get("HND_TEST_S3_BUCKET")
    if not endpoint or not bucket:
        pytest.skip(
            "set HND_TEST_S3_ENDPOINT_URL and HND_TEST_S3_BUCKET to a disposable S3 bucket"
        )
    pytest.importorskip("boto3")

    return S3CompatibleObjectStore(
        bucket=bucket,
        endpoint_url=endpoint,
        region=os.environ.get("HND_TEST_S3_REGION", "us-east-1"),
        access_key=os.environ.get("HND_TEST_S3_ACCESS_KEY"),
        secret_key=os.environ.get("HND_TEST_S3_SECRET_KEY"),
        prefix=os.environ.get("HND_TEST_S3_PREFIX", "contract-tests"),
    )


def test_s3_object_store_round_trip(s3_store: S3CompatibleObjectStore) -> None:
    store = s3_store
    reference = store.put_asset_bytes(
        "integration-test",
        b"production-object-store-contract",
        name=f"round-trip-{uuid.uuid4().hex}",
        suffix=".bin",
    )
    try:
        assert store.exists(reference)
        assert store.get(reference) == b"production-object-store-contract"
        if os.environ.get("HND_TEST_S3_REQUIRE_ENCRYPTION") == "1":
            metadata = store._client.head_object(Bucket=store._bucket, Key=store._key(reference))
            assert metadata.get("ServerSideEncryption") == "AES256"
    finally:
        store.delete(reference)
    assert not store.exists(reference)


def test_s3_owner_purge_preserves_other_owners(s3_store: S3CompatibleObjectStore) -> None:
    store = s3_store
    owner = "integration-" + uuid.uuid4().hex
    refs = [
        store.put_image_bytes(owner, b"image"),
        store.put_region_crop(owner, b"crop"),
        store.put_adapter_weights(owner, b"adapter"),
        store.put_asset_bytes(owner, b"asset"),
    ]
    other = store.put_image_bytes(owner + "-other", b"other-owner")
    try:
        store.delete_owner(owner)
        assert all(not store.exists(ref) for ref in refs)
        assert store.get(other) == b"other-owner"
    finally:
        for ref in [*refs, other]:
            store.delete(ref)
