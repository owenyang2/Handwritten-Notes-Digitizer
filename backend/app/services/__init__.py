"""Backend services (Recognition, Correction, Calibration, Personalization,
Evaluation, Export, Auth). Concrete implementations land in later tasks.

The Auth + Session layer (task 3.1) is implemented here: ``auth`` holds the
framework-agnostic core (password hashing, session store, ``AuthService``) and
``auth_api`` holds the FastAPI login route and the ``get_current_user`` gate.
"""

from .auth import (
    AuthError,
    AuthService,
    InvalidCredentials,
    SessionStore,
    hash_password,
    verify_password,
)

__all__ = [
    "AuthError",
    "AuthService",
    "InvalidCredentials",
    "SessionStore",
    "hash_password",
    "verify_password",
]
