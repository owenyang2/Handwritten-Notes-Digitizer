"""Stable API error codes shared by OpenAPI, web, and native clients.

Human messages may change. These codes must remain backward compatible for at
least two released mobile versions.
"""

from __future__ import annotations

from typing import Any

ERROR_CODES = {
    "upload_too_large": "The upload exceeds the configured size limit.",
    "upload_expired": "The resumable upload session expired.",
    "upload_hash_mismatch": "A declared digest did not match received bytes.",
    "upload_incomplete": "The upload parts are missing or not contiguous.",
    "upload_conflict": "The upload session cannot accept that mutation.",
    "model_unavailable": "No verified recognition artifact is loaded.",
    "revision_conflict": "The document version does not match the client base.",
    "consent_required": "This action requires an explicit training consent choice.",
    "quota_exceeded": "The account has exceeded a storage or compute quota.",
    "sync_cursor_stale": "The client cursor is older than the compacted floor.",
    "confirmation_required": "A destructive account action was not confirmed.",
    "ink_integrity_failure": "The ink bundle hash did not match canonical bytes.",
    "labels_unverified": "Real handwriting images cannot use unverified labels.",
}


def error_detail(code: str, message: str | None = None, **extra: Any) -> dict[str, Any]:
    if code not in ERROR_CODES:
        raise KeyError(f"unknown error code {code}")
    payload: dict[str, Any] = {
        "code": code,
        "message": message or ERROR_CODES[code],
    }
    payload.update(extra)
    return payload


def http_error(status_code: int, code: str, message: str | None = None, **extra: Any):
    """Build a FastAPI ``HTTPException`` with a stable client error body."""
    from fastapi import HTTPException

    return HTTPException(
        status_code=status_code,
        detail=error_detail(code, message, **extra),
    )
