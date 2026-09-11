# Colab / Kaggle launcher

Use the same local training CLI from a notebook rather than duplicating training code.

```python
# 1. Clone the repo and install the CUDA extra requirements on the GPU runtime.
# 2. Download only the licensed subset you intend to train on.
# 3. Copy data onto local runtime storage.
# 4. Run:

# !PYTHONPATH=backend python -m training.cli --manifest data/mathwriting_subset.jsonl --output /content/checkpoints/compact.pt --epochs 1

# 5. Copy checkpoints to durable private storage before the runtime expires.
```

Do not keep the only checkpoint on an ephemeral VM.
