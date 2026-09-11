# General English Evaluation Set

**Version:** `1.0.0` (see `GENERAL_EVAL_SET_VERSION` in `general_eval_set.py`)
**Manifest:** `app/ml/general_eval_manifest.json`
**Loader:** `app/ml/general_eval_set.py` → `load_general_eval_set()`

## Purpose

This is the fixed, held-aside **general English evaluation set** used by the
personalization guardrails. After a per-user `Adapter` is trained, the
`Evaluation_Service` measures the adapter's CER and WER on this set so the
`Personalization_Service` can detect and reject adapters that regress general
recognition accuracy (catastrophic forgetting).

- **Requirement 6.2** — When adapter training completes, the Evaluation_Service
  measures the CER and WER of the new adapter on a general English evaluation
  set.
- This set is the reference point for the accept/reject guardrails in
  **Requirements 6.3 / 6.4** (reject if general CER worsens by > 1.0 pp or
  general WER worsens by > 2.0 pp relative to the base model).

## Guarantees

1. **Held aside.** These samples are reserved exclusively for guardrail
   evaluation. They are **never** used to train the shared base model
   (task 6.3) nor any per-user adapter, so measurements on the set are an
   unbiased estimate of general recognition quality.
2. **Fixed and versioned.** Membership and references are pinned in the
   checked-in manifest and tagged with a semantic `version`. A SHA-256
   **content digest** (`general_eval_set_digest()`) pins the exact set a given
   measurement was computed against. The loader refuses to load a manifest
   whose declared `version` does not match the pinned
   `GENERAL_EVAL_SET_VERSION`, so the set cannot drift silently.
3. **Deterministic.** `load_general_eval_set()` always returns the samples in a
   stable order (sorted by `sample_id`) regardless of how the manifest is
   written, and the result is cached for the process lifetime.

The checked-in file contains references only; it does not ship licensed
handwriting pixels. Adapter promotion therefore uses
`load_verified_eval_pairs()`, which returns no pairs until matching verified
fixtures are installed. This deliberately makes the guardrail unavailable
rather than pairing unrelated IAM images with these authored sentences. The
synthetic fixtures used by tests require an explicit test-mode opt-in.

## Provenance

| Field | Value |
|-------|-------|
| Base dataset | IAM Handwriting Database |
| Base dataset version | 3.0 (line-level) |
| Source split | `test` |
| Selection method | A fixed subset held aside from the IAM line-level test split, never used for base-model or adapter training. |

**Licensing note.** The reference transcriptions in the manifest are original,
general-English sentences authored for this evaluation harness; they are **not**
verbatim IAM/LOB corpus transcriptions, in order to respect the dataset's
licensing. The `sample_id`s follow the IAM line-id naming convention
(`form-line`, e.g. `a01-000u-00`) so that, when a licensed copy of the IAM
dataset is present, the corresponding handwriting line images can be located and
paired with these references by id.

## Usage

```python
from app.ml.general_eval_set import load_general_eval_set, general_eval_set_digest

eval_set = load_general_eval_set()
print(eval_set.version)          # "1.0.0"
print(len(eval_set))             # number of held-aside samples
print(eval_set.digest)           # content digest pinning this exact set

for sample in eval_set.samples:  # deterministic order (sorted by sample_id)
    reference = sample.text      # ground-truth transcription
    # hypothesis = htr_engine.recognize(image_for(sample.sample_id), adapter)
    # cer = evaluation_service.cer(reference, hypothesis)  # (task 15)

# Pin which set a stored measurement used:
record_general_eval(version=eval_set.version, digest=general_eval_set_digest())
```

The `Evaluation_Service` (task 15) consumes the references here together with
the HTR engine's hypotheses to compute the general-set CER/WER that the
guardrails (task 14.5) act on.

## Versioning policy

When the set's membership or any reference text changes:

1. Edit `general_eval_manifest.json`.
2. Bump both the manifest `version` field and `GENERAL_EVAL_SET_VERSION` in
   `general_eval_set.py` (they must match or loading fails).
3. The content digest changes automatically, so any newly stored evaluation
   result is traceable to the new set.
