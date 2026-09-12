"""Machine-learning runtime: HTR_Engine (CNN+BiLSTM+CTC), base model, adapters,
and the training worker. Concrete implementations land in later tasks.

Exposes the base-model architecture (:class:`CRNN`), its pinned configuration
(:class:`HTRModelConfig`), and the deterministic base-model training procedure
(:func:`train_base_model`) for the recognition and training subsystems
(Req 11.2, 11.3)."""

from app.ml.htr_config import (
    ALPHABET,
    BLANK_INDEX,
    DEFAULT_HTR_CONFIG,
    AugmentationConfig,
    HTRModelConfig,
    TrainingConfig,
)
from app.ml.htr_model import CRNN, CNNFeatureExtractor, build_model
from app.ml.iam_dataset import (
    DATASET_ROOT_ENV,
    IAM_SPLIT_MANIFEST_PATH,
    PARTITIONS,
    IamSampleRef,
    IamSplitManifest,
    LabelsUnverifiedError,
    load_split_manifest,
    load_training_samples,
    synthetic_image,
)
from app.ml.train_base import (
    DEFAULT_IAM_SPLIT,
    IAM_DATASET_VERSION,
    LabeledSample,
    SplitSpec,
    TrainingProvenance,
    TrainingResult,
    default_model_dir,
    UnsupportedLabelError,
    encode_text,
    freeze_base_model,
    load_base_model,
    save_base_model_artifact,
    train_base_model,
)

__all__ = [
    "ALPHABET",
    "BLANK_INDEX",
    "DEFAULT_HTR_CONFIG",
    "AugmentationConfig",
    "HTRModelConfig",
    "TrainingConfig",
    "CRNN",
    "CNNFeatureExtractor",
    "build_model",
    # IAM dataset pipeline and fixed split (task 6.2)
    "DATASET_ROOT_ENV",
    "IAM_SPLIT_MANIFEST_PATH",
    "PARTITIONS",
    "IamSampleRef",
    "IamSplitManifest",
    "LabelsUnverifiedError",
    "load_split_manifest",
    "load_training_samples",
    "synthetic_image",
    # Base-model training procedure (task 6.3)
    "DEFAULT_IAM_SPLIT",
    "IAM_DATASET_VERSION",
    "LabeledSample",
    "SplitSpec",
    "TrainingProvenance",
    "TrainingResult",
    "default_model_dir",
    "UnsupportedLabelError",
    "encode_text",
    "freeze_base_model",
    "load_base_model",
    "save_base_model_artifact",
    "train_base_model",
]
