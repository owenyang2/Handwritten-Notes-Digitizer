# Model and dataset artifact boundary

Licensed handwriting pixels, private notes, model weights, checkpoints, and
evaluation exports are intentionally excluded from Git. The application code
must remain usable without them and must fail closed when they are absent.

## Local and container paths

- Local operator-supplied artifacts: `backend/var/objectstore/models/`
- Legacy prose artifact: `models/base_model/base_model.pt` plus its metadata
- Formula artifact: `models/unimernet_small/` or
  `models/compact_math/checkpoint.pt`
- Docker production profile: the model directory is a read-only host bind
  mount at `/app/var/objectstore/models`; user documents and crops use the
  configured filesystem/S3 object store.

The artifact directories are ignored by Git. Verify their contents with the
repository's doctor script before enabling recognition; a file existing at the
right path is not enough. The model manifest must record the source/revision,
license, SHA-256/tree digest, processor/configuration, and evaluation report.

## Staging intake

1. Obtain the dataset/checkpoint under an approved license and retain the
   source terms with the deployment record.
2. Build a matched, cryptographically pinned dataset manifest. Never pair an
   image and transcription by filename convention alone.
3. Render random image/label pairs for human verification, then create a
   disjoint holdout manifest with its own digest.
4. Copy the model and manifest to staging-only storage, run the model probe and
   benchmark report, and sign or otherwise access-control the resulting record.
5. Set the artifact/configuration reference in staging and confirm `/health`
   reports the actual loaded digest. If any check fails, leave the capability
   unavailable.

Do not treat a training script, a synthetic fixture, a historical runbook
entry, or a successful import/export test as recognition-quality evidence.
