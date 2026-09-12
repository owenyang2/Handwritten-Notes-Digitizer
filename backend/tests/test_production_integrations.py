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
    finally:
        engine.dispose()


def test_minio_s3_object_store_round_trip() -> None:
    endpoint = os.environ.get("HND_TEST_S3_ENDPOINT_URL")
    bucket = os.environ.get("HND_TEST_S3_BUCKET")
    if not endpoint or not bucket:
        pytest.skip(
            "set HND_TEST_S3_ENDPOINT_URL and HND_TEST_S3_BUCKET to a disposable MinIO bucket"
        )
    pytest.importorskip("boto3")

    store = S3CompatibleObjectStore(
        bucket=bucket,
        endpoint_url=endpoint,
        region=os.environ.get("HND_TEST_S3_REGION", "us-east-1"),
        access_key=os.environ.get("HND_TEST_S3_ACCESS_KEY"),
        secret_key=os.environ.get("HND_TEST_S3_SECRET_KEY"),
        prefix=os.environ.get("HND_TEST_S3_PREFIX", "contract-tests"),
    )
    reference = store.put_asset_bytes(
        "integration-test",
        b"production-object-store-contract",
        name=f"round-trip-{uuid.uuid4().hex}",
        suffix=".bin",
    )
    try:
        assert store.exists(reference)
        assert store.get(reference) == b"production-object-store-contract"
    finally:
        store.delete(reference)
    assert not store.exists(reference)
