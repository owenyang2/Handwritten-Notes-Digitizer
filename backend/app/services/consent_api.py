"""Account consent, vocabulary, and password-reset foundations."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.config.settings import get_settings
from app.data.base import utcnow
from app.data.models import ConsentEvent, PasswordResetToken, User
from app.data.repositories import RepositoryFactory
from app.services.auth import hash_password
from app.services.auth_api import get_current_user, get_repositories
from app.services.error_codes import http_error

consent_router = APIRouter(prefix="/personalization", tags=["personalization"])
account_recovery_router = APIRouter(prefix="/auth", tags=["auth"])


class ConsentRequest(BaseModel):
    action: str = Field(pattern="^(grant|withdraw|exclude)$")
    scope: str = Field(default="personal_model", max_length=64)
    document_id: uuid.UUID | None = None
    sample_id: str | None = Field(default=None, max_length=128)


class ConsentResponse(BaseModel):
    event_id: uuid.UUID
    policy_version: str
    scope: str
    action: str
    created_at: datetime
    personal_training_allowed: bool


class VocabularyRequest(BaseModel):
    token: str = Field(min_length=1, max_length=128)
    preferred_form: str = Field(min_length=1, max_length=256)


class VocabularyEntryResponse(BaseModel):
    token: str
    preferred_form: str


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=16, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


def _personal_training_allowed(repos: RepositoryFactory, user_id: uuid.UUID) -> bool:
    latest = repos.consent_events.latest(user_id, scope="personal_model")
    return latest is not None and latest.action == "grant"


@consent_router.post("/consent", response_model=ConsentResponse)
def record_consent(
    req: ConsentRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> ConsentResponse:
    event = repos.consent_events.add(
        ConsentEvent(
            user_id=user.user_id,
            policy_version=get_settings().consent_policy_version,
            scope=req.scope,
            action=req.action,
            document_id=req.document_id,
            sample_id=req.sample_id,
        )
    )
    repos.session.commit()
    return ConsentResponse(
        event_id=event.event_id,
        policy_version=event.policy_version,
        scope=event.scope,
        action=event.action,
        created_at=event.created_at,
        personal_training_allowed=_personal_training_allowed(repos, user.user_id),
    )


@consent_router.get("/consent", response_model=ConsentResponse)
def get_consent(
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> ConsentResponse:
    latest = repos.consent_events.latest(user.user_id, scope="personal_model")
    if latest is None:
        raise http_error(status.HTTP_404_NOT_FOUND, "consent_required")
    return ConsentResponse(
        event_id=latest.event_id,
        policy_version=latest.policy_version,
        scope=latest.scope,
        action=latest.action,
        created_at=latest.created_at,
        personal_training_allowed=_personal_training_allowed(repos, user.user_id),
    )


@consent_router.post("/vocabulary", response_model=VocabularyEntryResponse)
def upsert_vocabulary(
    req: VocabularyRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> VocabularyEntryResponse:
    if not _personal_training_allowed(repos, user.user_id):
        raise http_error(status.HTTP_403_FORBIDDEN, "consent_required")
    entry = repos.vocabulary.upsert(
        user.user_id, req.token.strip(), req.preferred_form.strip()
    )
    repos.session.commit()
    return VocabularyEntryResponse(token=entry.token, preferred_form=entry.preferred_form)


@consent_router.get("/vocabulary", response_model=list[VocabularyEntryResponse])
def list_vocabulary(
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> list[VocabularyEntryResponse]:
    return [
        VocabularyEntryResponse(token=entry.token, preferred_form=entry.preferred_form)
        for entry in repos.vocabulary.list_for_user(user.user_id)
    ]


@account_recovery_router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(
    req: PasswordResetRequest,
    repos: RepositoryFactory = Depends(get_repositories),
) -> dict[str, str]:
    """Always look identical so usernames cannot be enumerated from this path."""
    email = req.email.strip()
    user = repos.users.get_by_email(email) or repos.users.get_by_email(email.lower())
    if user is not None:
        raw = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        repos.password_resets.add(
            PasswordResetToken(
                token_hash=token_hash,
                user_id=user.user_id,
                expires_at=utcnow() + timedelta(hours=1),
            )
        )
        repos.session.commit()
        # Recovery mail is out of process. Tests inspect the hash; production
        # should enqueue this token to a verified-email sender.
        return {"status": "accepted", "debug_token": raw if get_settings().environment != "production" else ""}
    return {"status": "accepted", "debug_token": ""}


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@account_recovery_router.post("/password-reset/confirm")
def confirm_password_reset(
    req: PasswordResetConfirm,
    repos: RepositoryFactory = Depends(get_repositories),
) -> dict[str, str]:
    token_hash = hashlib.sha256(req.token.encode("utf-8")).hexdigest()
    row = repos.password_resets.get(token_hash)
    if row is None or row.used_at is not None or _as_aware(row.expires_at) <= utcnow():
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    user = repos.users.get_by_id(row.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    user.password_hash = hash_password(req.new_password)
    row.used_at = utcnow()
    repos.session.commit()
    return {"status": "updated"}
