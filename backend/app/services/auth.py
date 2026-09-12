"""Authentication and session layer (framework-agnostic core).

This module implements the design's *Auth + Session Layer* (see design.md,
"Components and Interfaces"): it authenticates users and provides the building
blocks that gate image, correction, and calibration submissions so that
unauthenticated requests are rejected before any data is stored (Req 1.5, 10.1,
10.4).

It deliberately contains no FastAPI / HTTP concepts so the logic is easy to
unit-test and reuse. The HTTP wiring (the ``/auth/login`` route and the
``get_current_user`` dependency that gates protected endpoints) lives in
``app.services.auth_api``.

Password hashing
----------------
Credentials are hashed with PBKDF2-HMAC-SHA256 from the standard library
(:func:`hashlib.pbkdf2_hmac`). This keeps the dependency surface minimal (no
bcrypt/argon2 wheel required) while still using a salted, iterated KDF.
Verification uses :func:`hmac.compare_digest` for a constant-time comparison.

The encoded hash is a single self-describing string::

    pbkdf2_sha256$<iterations>$<base64-salt>$<base64-derived-key>

so the iteration count and salt travel with the stored credential and can be
upgraded over time without a schema change.

Sessions / tokens
------------------
:class:`SessionStore` issues opaque bearer tokens (``secrets.token_urlsafe``)
mapped to a user id and persists them in the relational database. Tokens carry
a fixed TTL, survive process restarts, and are shared by every application
worker using that database.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import uuid
from typing import Optional
import datetime as _dt

from sqlalchemy.orm import Session
from app.data.models import User, UserSession
from app.data.base import utcnow
from app.data.repositories import UserRepository

# --- Password hashing parameters ---------------------------------------------

PBKDF2_ALGORITHM = "pbkdf2_sha256"
# Iteration count for PBKDF2. High enough to be a meaningful work factor while
# keeping a handful of hashes in the test suite fast.
PBKDF2_ITERATIONS = 200_000
_SALT_BYTES = 16
_TOKEN_BYTES = 32

# A pre-computed hash of a random throwaway password. ``authenticate`` verifies
# against this when a username is unknown so the response time does not reveal
# whether the username exists (mitigates a user-enumeration timing oracle).
_DUMMY_HASH: str  # assigned after hash_password is defined.


class AuthError(Exception):
    """Base class for authentication failures."""


class InvalidCredentials(AuthError):
    """Raised when a username/password pair does not match a stored user."""


def hash_password(
    password: str,
    *,
    iterations: int = PBKDF2_ITERATIONS,
    salt: Optional[bytes] = None,
) -> str:
    """Return a self-describing PBKDF2-HMAC-SHA256 hash of ``password``.

    A fresh random salt is generated unless one is supplied (supplying a salt is
    intended for tests/verification only). The returned string encodes the
    algorithm, iteration count, salt, and derived key so it is fully
    self-contained.
    """
    if not isinstance(password, str):
        raise TypeError("password must be a string")
    if salt is None:
        salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return "{algo}${iters}${salt}${hash}".format(
        algo=PBKDF2_ALGORITHM,
        iters=iterations,
        salt=base64.b64encode(salt).decode("ascii"),
        hash=base64.b64encode(derived).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check that ``password`` matches the ``encoded`` hash.

    Returns ``False`` for any malformed or unrecognized encoded value rather
    than raising, so callers can treat it as a simple boolean gate.
    """
    if not isinstance(password, str) or not isinstance(encoded, str):
        return False
    parts = encoded.split("$")
    if len(parts) != 4:
        return False
    algo, iters_s, salt_b64, hash_b64 = parts
    if algo != PBKDF2_ALGORITHM:
        return False
    try:
        iterations = int(iters_s)
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(hash_b64, validate=True)
    except (ValueError, binascii.Error):
        return False
    if iterations <= 0:
        return False
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(derived, expected)


_DUMMY_HASH = hash_password("dummy-password-for-timing-equalization")


class SessionStore:
    """Store of opaque bearer tokens mapped to user ids with TTL expiry.

    Persists sessions to the database via the ``UserSession`` model.
    Tokens expire after ``token_ttl_seconds`` (default: 7 days).
    """

    # Default session lifetime: 7 days
    DEFAULT_TTL: float = 7 * 24 * 60 * 60

    def __init__(self, db_session: Session, token_ttl_seconds: float | None = None) -> None:
        self._db_session = db_session
        self._ttl = token_ttl_seconds if token_ttl_seconds is not None else self.DEFAULT_TTL

    def create(self, user_id: uuid.UUID) -> str:
        """Issue and store a new opaque token for ``user_id``."""
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        expires_at = utcnow() + _dt.timedelta(seconds=self._ttl)
        session_record = UserSession(
            token=token,
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            session_id=uuid.uuid4(),
            user_id=user_id,
            expires_at=expires_at,
            last_seen_at=utcnow(),
        )
        self._db_session.add(session_record)
        self._db_session.commit()
        return token

    def resolve(self, token: Optional[str]) -> Optional[uuid.UUID]:
        """Return the user id for ``token`` or ``None`` if unknown/empty/expired."""
        if not token:
            return None

        record = self._db_session.get(UserSession, token)
        if record is None:
            return None

        if getattr(record, "revoked_at", None) is not None:
            return None

        expires_at = record.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=_dt.timezone.utc)

        if utcnow() > expires_at:
            # Token expired — evict lazily.
            self._db_session.delete(record)
            self._db_session.commit()
            return None

        return record.user_id

    def revoke(self, token: Optional[str]) -> bool:
        """Invalidate ``token``; return whether it existed and was not yet expired."""
        if not token:
            return False

        record = self._db_session.get(UserSession, token)
        if record is None:
            return False

        if hasattr(record, "revoked_at"):
            if record.revoked_at is not None:
                return False
            record.revoked_at = utcnow()
            self._db_session.commit()
            return True

        self._db_session.delete(record)
        self._db_session.commit()
        return True

    def purge_expired(self) -> int:
        """Remove all expired tokens. Returns the count of removed entries.

        Intended for periodic maintenance calls.
        """
        now = utcnow()
        
        # A bulk delete is safe here because session records have no state that
        # needs to remain in the identity map after maintenance.
        from sqlalchemy import delete
        stmt = delete(UserSession).where(UserSession.expires_at < now)
        result = self._db_session.execute(stmt)
        self._db_session.commit()
        return result.rowcount

    def clear(self) -> None:
        """Drop all tokens (used to isolate tests)."""
        from sqlalchemy import delete
        stmt = delete(UserSession)
        self._db_session.execute(stmt)
        self._db_session.commit()


class AuthService:
    """Coordinates credential checks and session issuance.

    Constructed with a per-request, user-scoped :class:`UserRepository` and a
    :class:`SessionStore`. It performs only reads against the repository
    (credential lookups); it never writes user data during authentication, which
    is what lets the auth gate reject unauthenticated requests while storing
    nothing (Req 1.5, 10.4).
    """

    def __init__(self, users: UserRepository, sessions: SessionStore) -> None:
        self._users = users
        self._sessions = sessions

    def register(self, username: str, email: str, password: str) -> User:
        """Create a user with a hashed password (helper for setup/seed flows)."""
        user = User(
            username=username,
            email=email,
            password_hash=hash_password(password),
        )
        return self._users.add(user)

    def authenticate(self, username: str, password: str) -> User:
        """Return the user iff the credentials are valid, else raise.

        When the username is unknown we still run a password verification against
        a dummy hash so the timing does not disclose user existence.
        """
        user = self._users.get_by_username(username)
        if user is None:
            verify_password(password, _DUMMY_HASH)
            raise InvalidCredentials("invalid username or password")
        if not verify_password(password, user.password_hash):
            raise InvalidCredentials("invalid username or password")
        return user

    def login(self, username: str, password: str) -> tuple[str, User]:
        """Authenticate and, on success, issue a session token.

        Returns ``(token, user)``. Raises :class:`InvalidCredentials` on failure.
        """
        user = self.authenticate(username, password)
        token = self._sessions.create(user.user_id)
        return token, user

    def resolve_token(self, token: Optional[str]) -> Optional[uuid.UUID]:
        """Map a presented token back to a user id (or ``None``)."""
        return self._sessions.resolve(token)

    def logout(self, token: Optional[str]) -> bool:
        """Revoke a session token; return whether it existed."""
        return self._sessions.revoke(token)


__all__ = [
    "AuthError",
    "InvalidCredentials",
    "PBKDF2_ALGORITHM",
    "PBKDF2_ITERATIONS",
    "hash_password",
    "verify_password",
    "SessionStore",
    "AuthService",
]
