"""Persistence layer: relational data models, repositories, and the object/image
store abstraction.

This package exposes the SQLAlchemy ORM models implementing the design's Data
Models section, the enumerations they use, the declarative ``Base``, engine /
session helpers, and the per-user-scoped repositories that enforce data
isolation at the data-access layer (defense in depth, Req 10.2/10.5).
"""

from .base import Base, new_uuid, utcnow
from .db import create_all, create_db_engine, create_session_factory
from .repositories import (
    AdapterRepository,
    DocumentRepository,
    EvaluationResultRepository,
    ImageRepository,
    RecognizedWordRepository,
    RepositoryFactory,
    TrainingPairRepository,
    TrainingRunRepository,
    UserRepository,
)
from .enums import (
    AdapterStatus,
    EvaluationAvailability,
    ImageFormat,
    ImageStatus,
    TrainingPairOrigin,
    TrainingRunState,
)
from .models import (
    Adapter,
    Document,
    EvaluationResult,
    Image,
    RecognizedWord,
    TrainingPair,
    TrainingRun,
    TrainingRunPair,
    User,
    UserSession,
)

__all__ = [
    "Base",
    "new_uuid",
    "utcnow",
    "create_all",
    "create_db_engine",
    "create_session_factory",
    # enums
    "AdapterStatus",
    "EvaluationAvailability",
    "ImageFormat",
    "ImageStatus",
    "TrainingPairOrigin",
    "TrainingRunState",
    # models
    "Adapter",
    "Document",
    "EvaluationResult",
    "Image",
    "RecognizedWord",
    "TrainingPair",
    "TrainingRun",
    "TrainingRunPair",
    "User",
    "UserSession",
    # repositories (per-user-scoped data-access layer)
    "UserRepository",
    "DocumentRepository",
    "ImageRepository",
    "RecognizedWordRepository",
    "TrainingPairRepository",
    "AdapterRepository",
    "TrainingRunRepository",
    "EvaluationResultRepository",
    "RepositoryFactory",
]
