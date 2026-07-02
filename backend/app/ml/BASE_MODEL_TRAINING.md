# Base Model Training Procedure (reproducible)

This document is the human-readable companion to `app/ml/train_base.py`. It
documents the dataset version, the data split, and the training configuration
used to produce the shared, frozen `Base_Model` of the HTR_Engine, satisfying
Requirements **11.2** (documented dataset/split/training procedure) and **11.3**
(re-running the procedure reproduces accuracy within tolerance).

## Dataset version

| Item | Value |
|------|-------|
| Source | IAM Handwriting Database (line-level), public English handwriting corpus |
| Pinned version | `IAM-Handwriting-DB-v3.0-lines` (`IAM_DATASET_VERSION` in `train_base.py`) |
| Granularity | Line images paired with ground-truth transcriptions |
| Label space | The pinned `ALPHABET` in `app/ml/htr_config.py` (index `0` reserved for the CTC blank) |

The dataset itself is **not** downloaded by the training module, and the real
IAM transcriptions are **not** checked into this repo (IAM's license does not
permit redistributing them). `app/ml/iam_split_manifest.json` — the manifest
checked into version control — therefore only contains 16 stub entries with
fabricated placeholder sentences, purely so the pipeline is exercisable in
tests without a licensed IAM copy. **Training on this checked-in manifest does
not produce a working recognizer** — see "Training on real IAM data" below for
how to do that with your own licensed download.

When no real dataset root is configured, `load_training_samples()` falls back
to a synthetic *placeholder* image — pure random noise, unrelated to the label
— purely to exercise the training loop's mechanics (shapes, batching, loss
computation) in tests. A model trained only on this fallback learns nothing
about handwriting and will emit blank/empty predictions on any real image.

### Training on real IAM data

1. Obtain a licensed copy of the IAM Handwriting Database (registration
   required at the FKI/IAM site) and download the line images (`lines.tgz`)
   and the `ascii.tgz` transcription package.
2. Extract both under a local, **gitignored** directory, e.g.:
   ```
   backend/datasets/
   ├── ascii/           # forms.txt, lines.txt (from ascii.tgz)
   └── lines/           # <writer>/<form>/<form>-<NN>.png (from lines.tgz)
   ```
   (`backend/datasets/` is already excluded via `.gitignore` — never commit
   real IAM images or transcriptions.)
3. Build a local manifest with real transcriptions (this is written under
   `datasets/`, never under `app/ml/`, so it's never committed):
   ```bash
   python -m app.ml.build_real_iam_manifest \
       --ascii-dir datasets/ascii \
       --images-dir datasets/lines \
       --output datasets/iam_real_manifest.json \
       --max-resized-width 800   # optional: bounds per-batch padding cost
   ```
   This groups lines by writer (via `forms.txt`) and assigns whole writers to
   train/val/test so the split stays writer-disjoint, then writes one entry
   per line with its real transcription from `lines.txt`.
4. Run the real training driver (prints per-epoch progress and writes an
   intermediate checkpoint every few epochs so a long CPU run is resilient to
   interruption):
   ```bash
   export HND_IAM_DATASET_ROOT=$(pwd)/datasets/lines
   python -m app.ml.run_real_training \
       --manifest datasets/iam_real_manifest.json \
       --max-epochs 16 \
       --checkpoint-every 2
   ```
   This is CPU-bound and slow (line-level CRNN training; expect on the order
   of 15–30 minutes per epoch over the full ~10k-line training split on a
   multi-core CPU, dominated by the BiLSTM over long sequences) — run it in
   the background (`nohup ... &`) and monitor the log.
5. The result overwrites the artifact at `default_model_dir()`
   (`<object_store_path>/models/base_model/`), the same location the app
   loads at startup (`app.ml.recognition.get_base_model`). Restart the backend
   (or clear its in-process model cache) to pick up the new weights.

## Data split

The split is fixed and recorded as provenance with every artifact
(`SplitSpec` / `DEFAULT_IAM_SPLIT`):

| Partition | Fraction | Purpose |
|-----------|----------|---------|
| train | 0.80 | base-model optimisation |
| validation | 0.10 | monitoring / model selection |
| test | 0.10 | fixed evaluation set for reproducibility checks (Req 11.3) |

The split is **writer-disjoint** (`name = "iam-aachen-writer-disjoint"`): the
same writer never appears in more than one partition, so test accuracy reflects
generalisation to unseen handwriting rather than memorised writers.

## Training configuration

Architecture and optimisation hyper-parameters are pinned in
`app/ml/htr_config.py` and consumed verbatim by `train_base_model`:

| Setting | Source | Default |
|---------|--------|---------|
| Architecture | `HTRModelConfig` | CNN feature extractor → BiLSTM → CTC head |
| Image height | `HTRModelConfig.image_height` | 32 px (variable width) |
| Optimiser | Adam | betas `(0.9, 0.999)` |
| Learning rate | `TrainingConfig.learning_rate` | `3e-4` |
| Batch size | `TrainingConfig.batch_size` | 32 |
| Max epochs | `TrainingConfig.max_epochs` | 80 |
| Weight decay | `TrainingConfig.weight_decay` | `1e-5` |
| Gradient clip | `TrainingConfig.grad_clip_norm` | 5.0 |
| Loss | `torch.nn.CTCLoss` | blank index 0, `zero_infinity=True` |
| Augmentation | `AugmentationConfig` | rotation/translate/scale/shear/noise (train only) |

## Reproducibility (Req 11.3)

The procedure is deterministic given the seed:

1. **Fixed seeds.** `configure_determinism()` (in `app/config/settings.py`)
   seeds Python, NumPy, and PyTorch and enables deterministic backends *before*
   the model is built, so weight initialisation is reproducible.
2. **Deterministic data ordering.** Batch shuffling uses a PyTorch `Generator`
   seeded from `Settings.random_seed`, and the loader runs in-process
   (`num_workers=0`), so the sample order is a pure function of the seed.
3. **Pinned configuration.** All hyper-parameters come from `htr_config.py`.
4. **Recorded provenance.** Each artifact is written with a
   `base_model.meta.json` capturing dataset version, split, model + training
   configuration, seed, epochs, samples seen, final loss, and framework
   versions (`TrainingProvenance`).

Re-running with the same dataset and configuration therefore produces a base
model whose accuracy on the fixed test split differs only within the configured
tolerance.

## Artifact location

The shared frozen base model is written to a documented path under the object
store root (see `default_model_dir`):

```
<object_store_path>/models/base_model/
├── base_model.pt        # weights state_dict + model_config + frozen flag
└── base_model.meta.json # TrainingProvenance (dataset version, split, config, seed, ...)
```

`object_store_path` defaults to `./var/objectstore` and is configurable via the
`HND_OBJECT_STORE_PATH` environment variable.

The saved model is **frozen**: `freeze_base_model` sets every parameter's
`requires_grad = False` and switches the model to eval mode. Personalization
later composes a small per-user adapter on top of this frozen base (Req 5.2) —
the base parameters themselves never change after training.

## Running the procedure

As an importable function:

```python
from app.ml.train_base import train_base_model
from app.ml.iam_dataset import load_training_samples  # provided by task 6.2

result = train_base_model(load_training_samples())
print(result.weights_path, result.provenance.final_train_loss)
```

As a CLI:

```bash
python -m app.ml.train_base --output-dir ./var/objectstore/models/base_model
```

A lightweight smoke test (`tests/test_train_base.py`) exercises the full
procedure on a tiny synthetic dataset, so the documented path is runnable
without downloading IAM.
