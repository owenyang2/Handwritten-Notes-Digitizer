"""Driver script: train the base model on the real, locally-downloaded IAM data.

Not part of the runtime request path — a one-off local script for producing a
genuinely-trained base model artifact from the manifest built by
``build_real_iam_manifest.py``. Prints per-epoch progress and writes an
intermediate checkpoint every few epochs so a long CPU run can be monitored
and is resilient to interruption.

Usage
-----
```bash
HND_IAM_DATASET_ROOT=$(pwd)/datasets/lines python -m app.ml.run_real_training \\
    --manifest datasets/iam_real_manifest.json \\
    --max-epochs 12 \\
    --checkpoint-every 2
```
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import torch

from app.ml.htr_model import CRNN
from app.ml.iam_dataset import load_training_samples
from app.ml.train_base import (
    DEFAULT_IAM_SPLIT,
    default_model_dir,
    save_base_model_artifact,
    train_base_model,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--max-epochs", type=int, default=12)
    parser.add_argument("--checkpoint-every", type=int, default=2)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    output_dir = args.output_dir or default_model_dir()

    print(f"[run_real_training] loading real training samples from {args.manifest}", flush=True)
    t0 = time.time()
    samples = load_training_samples(
        split=DEFAULT_IAM_SPLIT,
        partition="train",
        manifest_path=args.manifest,
        synthetic=False,
    )
    print(
        f"[run_real_training] loaded {len(samples)} real training samples in "
        f"{time.time() - t0:.1f}s",
        flush=True,
    )

    epoch_start = time.time()

    def on_epoch_end(epoch: int, loss: float, model: CRNN) -> None:
        nonlocal epoch_start
        elapsed = time.time() - epoch_start
        print(
            f"[run_real_training] epoch {epoch + 1}/{args.max_epochs} "
            f"loss={loss:.4f} epoch_time={elapsed:.1f}s",
            flush=True,
        )
        epoch_start = time.time()
        if (epoch + 1) % args.checkpoint_every == 0:
            # Best-effort intermediate checkpoint so progress survives an
            # interruption; the final call to train_base_model overwrites
            # this with the frozen, fully-provenanced artifact at the end.
            was_training = model.training
            model.eval()
            torch.save(
                {
                    "state_dict": model.state_dict(),
                    "model_config": model.config.to_dict(),
                    "frozen": False,
                },
                output_dir / "base_model.checkpoint.pt",
            )
            if was_training:
                model.train()
            print(
                f"[run_real_training] wrote intermediate checkpoint at epoch {epoch + 1}",
                flush=True,
            )

    result = train_base_model(
        samples,
        output_dir=output_dir,
        max_epochs=args.max_epochs,
        split=DEFAULT_IAM_SPLIT,
        on_epoch_end=on_epoch_end,
    )

    checkpoint_path = output_dir / "base_model.checkpoint.pt"
    if checkpoint_path.exists():
        checkpoint_path.unlink()

    print(
        f"[run_real_training] DONE. Saved to {result.weights_path} "
        f"(epochs={result.provenance.epochs_run}, "
        f"final_loss={result.provenance.final_train_loss:.4f})",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
