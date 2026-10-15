# Math-notes model card

## Scope

The product has separate recognition boundaries:

- `unimernet-small` is the first pretrained image-to-LaTeX adapter. It loads
  only a complete local snapshot and records the resolved Hub revision and
  snapshot SHA-256 in `model_artifact.json`.
- `compact-math` is the native PyTorch Track B research model: a 2-D CNN grid,
  two-dimensional positional information, a four-layer Transformer decoder,
  strict LaTeX tokenization, greedy decoding, bounded beam search, and an
  optional coverage variant.
- `crnn-ctc` remains the legacy prose-line backend for existing image workflows;
  its artifact is not a math model and is never used to answer a formula crop.

Missing, corrupt, incompatible, or unprobed artifacts report unavailable.
The application never initializes random weights as a production fallback.

## Intended use and limits

Use the math backends on complete formula crops. The page workflow retains the
original page and crop, lets a reviewer correct the source, and marks maximum
length as truncation. The model does not solve equations, infer missing strokes,
or decide whether an illegible symbol is mathematically plausible.

Diagrams are retained as image blocks. Mixed prose/math lines require reviewer
split decisions in the current release. Performance on personal notes is not
estimated by the checked-in synthetic smoke fixtures.

## Reproduction

1. Download a licensed snapshot with `scripts/download_unimernet.py`, or train
   a compact checkpoint with `python -m training.cli`.
2. Keep `model_artifact.json`, tokenizer/config, dataset digest, split, seed,
   device, and benchmark report beside the artifact.
3. Run a warm inference smoke test and `GET /health`; only a passed probe is
   advertised as loaded.

The CUDA lock is `training/requirements-cuda.txt`; optional VLM experiments use
`training/requirements-vlm.txt`. These environments are intentionally separate
from the CPU FastAPI runtime.

## Evaluation

Use `benchmarks.runner` on verified image/label pairs. Report normalized exact
match, LaTeX token error rate, renderability, truncation, critical structure
errors, page layout recall/order/end-to-end accuracy, latency, peak memory, and
paired page-level uncertainty. Do not present a synthetic or single-seed smoke
test as handwriting accuracy.
