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
    DocumentRevisionRepository,
    EvaluationResultRepository,
    ImageRepository,
    ModelArtifactRepository,
    RecognizedWordRepository,
    RecognitionJobRepository,
    ReviewRevisionRepository,
    TrainingExampleRepository,
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
    RecognitionJobState,
    TrainingExampleSplit,
    TrainingPairOrigin,
    TrainingRunState,
)
from .models import (
    Adapter,
    Document,
    EvaluationResult,
    Image,
    ImageLineReview,
    DocumentPage,
    DocumentRegion,
    DocumentRevision,
    RegionPrediction,
    RegionReview,
    ReviewRevision,
    RecognitionJob,
    TrainingExample,
    ModelArtifact,
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
    "RecognitionJobState",
    "TrainingExampleSplit",
    "TrainingPairOrigin",
    "TrainingRunState",
    # models
    "Adapter",
    "Document",
    "EvaluationResult",
    "Image",
    "ImageLineReview",
    "DocumentPage",
    "DocumentRegion",
    "DocumentRevision",
    "RegionPrediction",
    "RegionReview",
    "ReviewRevision",
    "RecognitionJob",
    "TrainingExample",
    "ModelArtifact",
    "RecognizedWord",
    "TrainingPair",
    "TrainingRun",
    "TrainingRunPair",
    "User",
    "UserSession",
    # repositories (per-user-scoped data-access layer)
    "UserRepository",
    "DocumentRepository",
    "DocumentRevisionRepository",
    "ImageRepository",
    "ModelArtifactRepository",
    "RecognizedWordRepository",
    "RecognitionJobRepository",
    "ReviewRevisionRepository",
    "TrainingExampleRepository",
    "TrainingPairRepository",
    "AdapterRepository",
    "TrainingRunRepository",
    "EvaluationResultRepository",
    "RepositoryFactory",
]
