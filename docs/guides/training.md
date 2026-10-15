# Training and evaluation

Start with [application setup](getting-started.md). For pretrained UniMERNet
and TrOCR inference, use [recognition setup](recognition-and-ipad.md).
This guide covers the optional compact formula and legacy CRNN research paths;
neither has a representative quality result. Models and licensed datasets are
supplied separately; keep private notes, pixels and checkpoints out of Git.

CPU smoke runs check plumbing. For longer training, use a separate NVIDIA/CUDA
environment. Keep whole-document train/dev/test splits disjoint, retain dataset
licenses and digests, and follow the [data card](../reference/data-card.md).

## Compact formula model

Use this route when you need a fully native PyTorch training path or want to
train on a permitted formula dataset.

Each JSONL record needs a stable ID, an image or InkML source, a normalized
LaTeX label, and an explicit split. A minimal shape is:

~~~json
{"sample_id":"mw-0001","image":"crops/mw-0001.png","rawLabel":"x^2","normalizedLabel":"x^2","split":"train","source_document_id":"page-group-1","page_id":"page-1"}
~~~

The loader rejects unsupported labels, missing sources, duplicate IDs, and a
source document that crosses train/dev/test splits. It rasterizes InkML
deterministically and includes source bytes and split metadata in the dataset
digest. The training command refuses to train by falling back to dev/test data.

Put the manifest and images outside Git, then run a CPU smoke/pilot from the
repository root:

~~~bash
PYTHONPATH=backend backend/.venv/bin/python -m training.cli --manifest data/mathwriting/manifest.jsonl --output backend/var/objectstore/models/compact_math/checkpoint.pt --epochs 1 --batch-size 2 --device cpu
~~~

For a CUDA training environment, keep it separate from the CPU web runtime.
Install the application dependencies needed by the training modules, then
replace the CPU PyTorch packages with the CUDA-matched versions. For example:

~~~bash
python3 -m venv training/.venv
training/.venv/bin/pip install -r backend/requirements.txt
training/.venv/bin/pip uninstall -y torch torchvision torchaudio
training/.venv/bin/pip install -r training/requirements-cuda.txt --index-url https://download.pytorch.org/whl/cu124
~~~

Verify CUDA before starting a long run:

~~~bash
training/.venv/bin/python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
~~~

Train with dynamic-width batches, AMP, gradient accumulation, and a resumable
checkpoint:

~~~bash
PYTHONPATH=backend training/.venv/bin/python -m training.cli --manifest data/mathwriting/manifest.jsonl --output backend/var/objectstore/models/compact_math/checkpoint.pt --epochs 16 --batch-size 8 --accumulate 4 --workers 0 --device cuda --amp --patience 5
~~~

If interrupted, resume from the checkpoint:

~~~bash
PYTHONPATH=backend training/.venv/bin/python -m training.cli --manifest data/mathwriting/manifest.jsonl --output backend/var/objectstore/models/compact_math/checkpoint.pt --resume backend/var/objectstore/models/compact_math/checkpoint.pt --epochs 16 --device cuda --amp
~~~

The checkpoint records the tokenizer/configuration, optimizer, scheduler, AMP
scaler, RNG state, epoch/data position, manifest path, and dataset digest.
After copying a completed checkpoint into the Docker-mounted model directory,
restart the backend and check /health.

Profile a completed checkpoint separately from accuracy:

~~~bash
PYTHONPATH=backend training/.venv/bin/python training/profile.py --checkpoint backend/var/objectstore/models/compact_math/checkpoint.pt --device cuda --steps 20
~~~

## Legacy prose model and personalization

These steps are not required for formula-only Math Notes review. They are
needed for the original line-level English recognizer and its per-user adapter
workflow.

### Train the shared prose model on licensed IAM data

Obtain IAM under its terms and extract it outside version control:

~~~text
backend/datasets/
├── ascii/       # forms.txt and lines.txt
└── lines/       # IAM line PNG hierarchy
~~~

Build a writer-disjoint manifest:

~~~bash
cd backend
PYTHONPATH=.. .venv/bin/python -m app.ml.build_real_iam_manifest --ascii-dir datasets/ascii --images-dir datasets/lines --output datasets/iam_real_manifest.json --max-resized-width 800
~~~

Run the real training driver. It is CPU-bound and may take a long time over
the full dataset:

~~~bash
HND_IAM_DATASET_ROOT="$PWD/datasets/lines" PYTHONPATH=.. .venv/bin/python -m app.ml.run_real_training --manifest datasets/iam_real_manifest.json --max-epochs 12 --checkpoint-every 2 --output-dir var/objectstore/models/base_model
~~~

The final artifact is base_model.pt plus base_model.meta.json. Restart the
backend after training. The checked-in IAM manifest and synthetic fallback are
only plumbing fixtures; they do not produce a useful recognizer.

### Personalization requirements

Personalization examples are created only from explicit user consent on saved
reviews. A real deployment also needs:

- a trained/frozen base prose artifact;
- enough consented examples to cross HND_PERSONALIZATION_TRIGGER_THRESHOLD;
- a held-out personal split;
- a verified general-English image/label fixture set for the catastrophic-
  forgetting guardrail.

Leave this setting false in production:

~~~dotenv
HND_ALLOW_SYNTHETIC_EVAL=false
~~~

The checked-in general evaluation manifest contains references but deliberately
does not contain licensed handwriting pixels. Do not pair arbitrary IAM images
with those references. Supply a separately verified fixture manifest with image
hashes and keep the test images out of training. Until that exists, adapter
promotion must remain unavailable rather than being justified by synthetic
noise.

The API surface includes status, training-example review, and a durable run
request at POST /personalization/runs; compact formula training remains the
offline training.cli workflow described above.

## Evaluation

For formula recognition, prepare a verified prediction JSONL file with at least
reference and prediction per row. Prefer also recording sample_id, page_id,
region_id, predicted_region_id, truncated, latency_ms, and peak_memory_bytes.

Run the benchmark report:

~~~bash
PYTHONPATH=backend backend/.venv/bin/python -m benchmarks.runner --predictions results/formula-test.predictions.jsonl --output results/formula-test.report.json --version 1.0.0 --split test
~~~

Use a development split for model selection and a separate final test split for
release claims. For personal notes, bootstrap by page rather than by crop so a
page with many regions cannot dominate the confidence interval. The report
includes normalized exact match, LaTeX token error rate, renderability,
critical-structure errors, truncation, page-level layout/order, and
end-to-end summaries.

Do not call the tiny compact smoke run or the synthetic fixtures an accuracy
result. They only prove that the pipeline can load data, train, checkpoint, and
run inference.

## Experiment record

Retain the artifact and dataset manifests/digests, split, tokenizer hash,
configuration, seed, hardware, training history, profiling output, predictions
and benchmark report. Synthetic fixtures establish execution only.

[Model card](../reference/model-card.md) ·
[Benchmark commands](../../benchmarks/README.md) ·
[CRNN training details](../../backend/app/ml/BASE_MODEL_TRAINING.md) ·
[General evaluation set](../../backend/app/ml/GENERAL_EVAL_SET.md)
