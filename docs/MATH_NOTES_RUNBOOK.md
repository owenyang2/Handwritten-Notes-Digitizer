# Math-notes implementation runbook

For a complete first-time setup, artifact installation procedure, and explicit
list of external requirements, see [MATH_NOTES_SETUP_GUIDE.md](MATH_NOTES_SETUP_GUIDE.md).

## Fast checks

```bash
make test
cd frontend && npm run typecheck && npm run build
cd ../backend && .venv/bin/alembic upgrade head && .venv/bin/alembic check
```

A clean checkout without model weights is expected to report degraded health.
Install a real snapshot or train a compact checkpoint before expecting formula
recognition to be available.

## PDF vertical slice

1. Start the backend and frontend.
2. Upload a multipage PDF. The original PDF, per-page PNG raster, dimensions,
   DPI, and transform metadata are persisted.
3. Drag a complete expression or use **Auto-detect page regions**.
4. Select a crop, inspect the original crop / editable LaTeX / KaTeX preview,
   and save. Check consent only when the crop should enter personalization.
5. Reopen the document from History and export the active reviewed revision as
   Markdown, LaTeX, HTML, or controlled PDF.

Recognition work uses database-backed jobs with bounded concurrency, ownership
checks, retries, deadlines, and startup recovery. A missing model produces an
explicit failed/unavailable job and still allows manual LaTeX entry.

## Model setup

```bash
(cd backend && python ../scripts/download_unimernet.py \
  --destination var/objectstore/models/unimernet_small \
  --revision main)
```

The script resolves `main` to an immutable Hub commit before writing the
manifest. For the native model:

```bash
cd backend
PYTHONPATH=.. .venv/bin/python -m training.cli \
  --manifest ../data/mathwriting_subset.jsonl \
  --output var/objectstore/models/compact_math/checkpoint.pt \
  --epochs 1 --device cuda --amp
```

Use only an explicit synthetic flag in test mode. Production promotion remains
unavailable until verified real evaluation image/label pairs are installed.

## Experiment record

Record the model artifact manifest, dataset manifest/digest, split, tokenizer
hash, config, seed, hardware, batch/accumulation settings, training history,
profiling output, predictions, and benchmark report. Compare E0/E1/E2/E3 with
matched data and decoding policies; run E4/E5 for the compact research track.
