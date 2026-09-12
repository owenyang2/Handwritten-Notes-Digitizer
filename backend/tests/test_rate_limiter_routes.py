"""Rate-limit classification for production ingest/sync/export routes.

These checks call ``_find_rule`` directly so they stay fast and do not require
Redis or a running application.
"""

from __future__ import annotations

from app.middleware.rate_limiter import _find_rule


def test_document_import_is_classified() -> None:
    matched = _find_rule("POST", "/documents/import")
    assert matched is not None
    assert matched[0] == "/documents/import"


def test_uploads_create_and_parts_are_classified() -> None:
    create = _find_rule("POST", "/uploads")
    complete = _find_rule("POST", "/uploads/abc/complete")
    part = _find_rule("PUT", "/uploads/abc/parts/1")
    assert create is not None and create[0] == "/uploads"
    assert complete is not None and complete[0] == "/uploads"
    assert part is not None and part[0] == "/uploads"


def test_page_recognition_paths_are_classified() -> None:
    recognition = _find_rule("POST", "/pages/11111111-1111-1111-1111-111111111111/recognition")
    jobs = _find_rule("POST", "/pages/page-1/recognition-jobs")
    assert recognition is not None and recognition[0] == "/pages/*/recognition"
    assert jobs is not None and jobs[0] == "/pages/*/recognition"


def test_sync_push_is_classified() -> None:
    matched = _find_rule("POST", "/sync/push")
    assert matched is not None
    assert matched[0] == "/sync/push"


def test_export_is_classified() -> None:
    matched = _find_rule("POST", "/export")
    assert matched is not None
    assert matched[0] == "/export"


def test_existing_auth_and_image_rules_still_match() -> None:
    assert _find_rule("POST", "/auth/login") == ("/auth/login", 5, 0.2)
    assert _find_rule("POST", "/auth/register") == ("/auth/register", 3, 0.05)
    assert _find_rule("POST", "/images") == ("/images", 10, 0.5)
    assert _find_rule("POST", "/images/first/recognize") == ("/recognize", 5, 0.33)
    assert _find_rule("POST", "/images/second/recognize") == ("/recognize", 5, 0.33)


def test_unrelated_gets_are_not_classified() -> None:
    assert _find_rule("GET", "/health") is None
    assert _find_rule("GET", "/documents/import") is None
    assert _find_rule("POST", "/sync/pull") is None
    assert _find_rule("POST", "/pages/abc/regions") is None
