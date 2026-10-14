"""FastAPI wiring for the Auth + Session layer.

This module exposes:

* ``POST /auth/login`` - authenticate a user and establish a session by issuing
  a bearer token (design API Surface, Req 10.1).
* ``get_current_user`` - the FastAPI dependency that **gates** protected
  endpoints (image, correction, and calibration submissions). It resolves the
  presented bearer token (Authorization header) or ``session`` cookie to a user
  and, when no valid credential is present, raises ``401`` with an
  authentication-required message *before* any route handler runs - so an
  unauthenticated submission stores nothing (Req 1.5, 10.1, 10.4).

The request-scoped dependencies (``get_db_session``, ``get_repositories``,
``get_session_store``, ``get_auth_service``) are defined here so other capability
routers added in later tasks can depend on ``get_current_user`` and reuse the
same per-user-scoped :class:`RepositoryFactory`. Tests override
``get_db_session`` and ``get_session_store`` via ``app.dependency_overrides`` to
run against an isolated database and a fresh session store.
"""

from __future__ import annotations

import re
import uuid
from functools import lru_cache
from typing import Iterator, Optional

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.middleware.rate_limiter import (
    RateLimitUnavailable,
    consume_subject_limit,
    login_lock_remaining,
    note_login_failure,
    note_login_success,
)
from app.data.db import create_db_engine, create_session_factory
from app.data.migrations import upgrade_database
from app.data.models import User
from app.data.repositories import RepositoryFactory
from app.config.settings import get_settings

from .auth import AuthService, InvalidCredentials, SessionStore

# Message returned whenever authentication is required but missing/invalid
# (Req 10.4: "return a message indicating that authentication is required").
AUTH_REQUIRED_MESSAGE = "Authentication is required"
INVALID_CREDENTIALS_MESSAGE = "Invalid username or password"
USERNAME_TAKEN_MESSAGE = "That username is already taken"
EMAIL_TAKEN_MESSAGE = "That email is already registered"
ACCOUNT_CONFLICT_MESSAGE = "That account already exists"

# Name of the cookie that carries the opaque session token. ``get_current_user``
# reads this cookie (see below), so login/register set it and logout clears it.
SESSION_COOKIE_NAME = "session"
# Session cookie lifetime in seconds (7 days) for the demo deployment.
SESSION_COOKIE_MAX_AGE = 7 * 24 * 60 * 60


def _set_session_cookie(response: Response, token: str) -> None:
    """Attach the session token as an HTTP cookie.

    ``httponly`` keeps the token out of JavaScript (mitigates XSS token theft);
    ``samesite=lax`` allows normal same-origin usage. ``secure`` is left off so
    the demo works over plain HTTP on localhost; enable it behind HTTPS.
    """
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=get_settings().environment == "production",
        path="/",
    )

router = APIRouter(tags=["auth"])


# --- Request-scoped dependency providers -------------------------------------





@lru_cache(maxsize=1)
def _default_session_factory() -> sessionmaker[Session]:
    """Lazily build the app's session factory from configured settings.

    Schema upgrades run before the first request so every runtime uses the
    checked-in Alembic history. The factory is cached for process lifetime.
    """
    database_url = get_settings().database_url
    upgrade_database(database_url)
    engine = create_db_engine(database_url)
    return create_session_factory(engine)


def get_db_session() -> Iterator[Session]:
    """Yield a request-scoped session, closing it when the request ends."""
    factory = _default_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def get_session_store(session: Session = Depends(get_db_session)) -> SessionStore:
    """Provide the session store bound to the current database session."""
    return SessionStore(session)


def get_repositories(
    session: Session = Depends(get_db_session),
) -> RepositoryFactory:
    """Provide the per-user-scoped repository factory for this request."""
    return RepositoryFactory(session)


def get_auth_service(
    repos: RepositoryFactory = Depends(get_repositories),
    sessions: SessionStore = Depends(get_session_store),
) -> AuthService:
    """Provide an :class:`AuthService` bound to this request's session."""
    return AuthService(repos.users, sessions)


# --- Token extraction + the auth gate ----------------------------------------


def _extract_token(
    authorization: Optional[str], session_cookie: Optional[str]
) -> Optional[str]:
    """Pull a bearer token from the Authorization header or session cookie."""
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    if session_cookie:
        return session_cookie
    return None


def _auth_required() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=AUTH_REQUIRED_MESSAGE,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    session: Optional[str] = Cookie(default=None),
    sessions: SessionStore = Depends(get_session_store),
    repos: RepositoryFactory = Depends(get_repositories),
) -> User:
    """Authentication gate for protected endpoints.

    Resolves the presented token to a user. Raises ``401`` with an
    authentication-required message when the token is missing, unknown, or
    points at a user that no longer exists. Because this runs as a dependency,
    the rejection happens before the gated handler executes, so an
    unauthenticated image/correction/calibration submission stores nothing
    (Req 1.5, 10.1, 10.4).
    """
    token = _extract_token(authorization, session)
    user_id = sessions.resolve(token)
    if user_id is None:
        raise _auth_required()
    user = repos.users.get_by_id(user_id)
    if user is None or user.disabled_at is not None:
        raise _auth_required()
    return user


# --- Login endpoint ----------------------------------------------------------


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LoginRequest(BaseModel):
    """Credentials submitted to ``POST /auth/login``.

    Password length is capped so an unauthenticated caller cannot force an
    arbitrarily expensive PBKDF2. Login still accepts existing shorter
    passwords; registration is where the minimum length is enforced.
    """

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def strip_username(cls, value: str) -> str:
        return value.strip()


class LoginResponse(BaseModel):
    """Session token issued on successful authentication."""

    access_token: str
    token_type: str = "bearer"
    user_id: uuid.UUID


class RegisterRequest(BaseModel):
    """Credentials submitted to ``POST /auth/register``."""

    username: str = Field(min_length=1, max_length=64)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def strip_username(cls, value: str) -> str:
        return value.strip()

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _EMAIL_RE.match(normalized):
            raise ValueError("email is not a valid email address")
        return normalized


class MeResponse(BaseModel):
    """The authenticated user's public profile."""

    user_id: uuid.UUID
    username: str
    email: str


@router.post(
    "/auth/register",
    response_model=LoginResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    payload: RegisterRequest,
    response: Response,
    auth: AuthService = Depends(get_auth_service),
    session: Session = Depends(get_db_session),
    repos: RepositoryFactory = Depends(get_repositories),
) -> LoginResponse:
    """Create a new account, establish a session, and set the session cookie.

    Rejects a duplicate username with ``409`` (without disclosing other data).
    On success the new user is committed, a bearer token is issued, and the
    ``session`` cookie is set so subsequent same-origin requests authenticate
    automatically.
    """
    username = payload.username.strip()
    email = payload.email.strip().lower()
    _enforce_subject_quota(email, "POST:/auth/register", 3, 0.05)
    _enforce_subject_quota(username, "POST:/auth/register", 3, 0.05)
    if not username or not payload.password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Username and password are required",
        )
    if repos.users.get_by_username(username) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=USERNAME_TAKEN_MESSAGE,
        )
    if repos.users.get_by_email(email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=EMAIL_TAKEN_MESSAGE,
        )
    try:
        user = auth.register(username, email, payload.password)
        session.commit()
    except IntegrityError:
        session.rollback()
        if repos.users.get_by_email(email) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=EMAIL_TAKEN_MESSAGE,
            )
        if repos.users.get_by_username(username) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=USERNAME_TAKEN_MESSAGE,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ACCOUNT_CONFLICT_MESSAGE,
        )
    token, _ = auth.login(username, payload.password)
    _set_session_cookie(response, token)
    return LoginResponse(access_token=token, user_id=user.user_id)


@router.post("/auth/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    response: Response,
    auth: AuthService = Depends(get_auth_service),
) -> LoginResponse:
    """Authenticate and establish a session, returning a bearer token.

    On invalid credentials returns ``401`` with a generic message (the same
    message for unknown username and wrong password, so existence is not
    disclosed). No user data is written during login. On success the ``session``
    cookie is set so subsequent same-origin requests authenticate automatically.
    """
    username = payload.username.strip()
    _raise_if_locked(username)
    _enforce_subject_quota(username, "POST:/auth/login", 5, 0.2)
    try:
        token, user = auth.login(username, payload.password)
    except InvalidCredentials:
        try:
            note_login_failure(username)
        except RateLimitUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication is temporarily unavailable.",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS_MESSAGE,
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        note_login_success(username)
    except RateLimitUnavailable:
        # The session is already issued. A Redis blip must not turn success into 503.
        pass
    _set_session_cookie(response, token)
    return LoginResponse(access_token=token, user_id=user.user_id)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    authorization: Optional[str] = Header(default=None),
    session_cookie: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    sessions: SessionStore = Depends(get_session_store),
) -> Response:
    """Revoke the presented session token and clear the session cookie."""
    token = _extract_token(authorization, session_cookie)
    sessions.revoke(token)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/auth/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user)) -> MeResponse:
    """Return the authenticated user's profile (used by the UI to check auth)."""
    return MeResponse(
        user_id=user.user_id, username=user.username, email=user.email
    )


def _enforce_subject_quota(
    subject: str, route_key: str, capacity: float, refill_rate: float
) -> None:
    """Apply the per-account auth bucket. IP quotas are enforced in middleware."""
    try:
        result = consume_subject_limit(subject, route_key, capacity, refill_rate)
    except RateLimitUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable.",
        ) from exc
    if result == "unavailable":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable.",
            headers={"Retry-After": "5"},
        )
    if result == "limited":
        retry_after = int(1.0 / refill_rate) if refill_rate > 0 else 60
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please slow down.",
            headers={"Retry-After": str(retry_after)},
        )


def _raise_if_locked(username: str) -> None:
    try:
        remaining = login_lock_remaining(username)
    except RateLimitUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable.",
        ) from exc
    if remaining:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please slow down.",
            headers={"Retry-After": str(remaining)},
        )


__all__ = [
    "router",
    "AUTH_REQUIRED_MESSAGE",
    "INVALID_CREDENTIALS_MESSAGE",
    "USERNAME_TAKEN_MESSAGE",
    "EMAIL_TAKEN_MESSAGE",
    "ACCOUNT_CONFLICT_MESSAGE",
    "SESSION_COOKIE_NAME",
    "get_current_user",
    "get_session_store",
    "get_db_session",
    "get_repositories",
    "get_auth_service",
    "LoginRequest",
    "LoginResponse",
    "RegisterRequest",
    "MeResponse",
]
