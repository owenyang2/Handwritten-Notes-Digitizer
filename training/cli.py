"""Reproducible compact image-to-LaTeX training and resume CLI.

The command is intentionally small enough for an RTX 3060 pilot but includes
the mechanics needed for a real experiment: validated labels, deterministic
splits/order, variable-width padding, AMP/accumulation, optimizer-step
scheduling, validation decoding, complete checkpoints, and resume support.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from functools import partial
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

from app.config import configure_determinism, get_settings
from app.ml.math.model import CompactMathConfig, CompactMathModel
from app.ml.math.tokenizer import LatexTokenizer, UnsupportedLatexError
from benchmarks.metrics import summarize
from training.checkpoint import capture_rng_state, load_checkpoint, restore_rng_state, save_checkpoint
from training.datasets import FormulaCropDataset, FormulaRecord, dataset_digest, load_formula_records
from training.losses import token_cross_entropy


class CropJsonDataset(FormulaCropDataset):
    """Backward-compatible JSONL dataset name used by early pilots."""

    def __init__(self, manifest: Path, tokenizer: LatexTokenizer, image_height: int) -> None:
        super().__init__(
            load_formula_records(manifest, tokenizer=tokenizer),
            tokenizer,
            image_height=image_height,
            max_width=None,
        )


def collate(batch: list[dict[str, Any]], pad_id: int) -> dict[str, Any]:
    """Pad image widths and decoder targets without losing valid-pixel masks."""
    if not batch:
        raise ValueError("cannot collate an empty batch")
    max_w = max(int(item["image"].size(-1)) for item in batch)
    max_t = max(int(item["tokens"].size(0)) for item in batch)
    height = int(batch[0]["image"].size(-2))
    images = torch.zeros(len(batch), 1, height, max_w, dtype=torch.float32)
    valid = torch.zeros(len(batch), height, max_w, dtype=torch.bool)
    tokens = torch.full((len(batch), max_t), pad_id, dtype=torch.long)
    for index, item in enumerate(batch):
        width = int(item["image"].size(-1))
        images[index, :, :, :width] = item["image"]
        valid[index, :, :width] = item["valid"]
        length = int(item["tokens"].size(0))
        tokens[index, :length] = item["tokens"]
    decoder_in = tokens[:, :-1]
    decoder_tgt = tokens[:, 1:]
    return {
        "images": images,
        "valid": valid,
        "decoder_in": decoder_in,
        "decoder_tgt": decoder_tgt,
        "token_pad": decoder_in == pad_id,
        "references": [str(item["latex"]) for item in batch],
        "sample_ids": [str(item["sample_id"]) for item in batch],
    }


def _worker_init(worker_id: int) -> None:
    seed = int(get_settings().random_seed) + worker_id
    random.seed(seed)
    np.random.seed(seed % (2**32 - 1))
    torch.manual_seed(seed)


def _tokenizer_from_payload(payload: dict[str, Any]) -> LatexTokenizer:
    mapping = payload.get("tokenizer")
    if not isinstance(mapping, dict):
        return LatexTokenizer.default()
    normalized = {str(token): int(index) for token, index in mapping.items()}
    return LatexTokenizer(token_to_id=normalized, id_to_token={index: token for token, index in normalized.items()})


def _config_from_payload(payload: dict[str, Any], fallback: CompactMathConfig) -> CompactMathConfig:
    values = payload.get("config")
    if not isinstance(values, dict):
        return fallback
    allowed = CompactMathConfig.__dataclass_fields__
    return CompactMathConfig(**{key: value for key, value in values.items() if key in allowed})


def _make_loader(
    records: Sequence[FormulaRecord],
    tokenizer: LatexTokenizer,
    config: CompactMathConfig,
    *,
    batch_size: int,
    shuffle: bool,
    workers: int,
    seed: int,
) -> DataLoader:
    dataset = FormulaCropDataset(records, tokenizer, image_height=config.image_height, max_width=config.max_width)
    generator = torch.Generator()
    generator.manual_seed(seed)
    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=shuffle,
        generator=generator,
        num_workers=workers,
        worker_init_fn=_worker_init if workers else None,
        # ``partial`` is picklable when workers are enabled; a lambda would
        # make the documented multi-worker path fail under spawn-based loaders.
        collate_fn=partial(collate, pad_id=tokenizer.pad_id),
        pin_memory=torch.cuda.is_available(),
    )


@torch.no_grad()
def evaluate(model: CompactMathModel, loader: DataLoader, *, device: torch.device, max_batches: int | None = None) -> dict[str, float | int | None]:
    model.eval()
    loss_total = 0.0
    batches = 0
    pairs: list[tuple[str, str]] = []
    truncation: list[bool] = []
    for batch in loader:
        if max_batches is not None and batches >= max_batches:
            break
        batches += 1
        images = batch["images"].to(device)
        valid = batch["valid"].to(device)
        targets = batch["decoder_tgt"].to(device)
        logits = model(
            images,
            batch["decoder_in"].to(device),
            valid_mask=valid,
            token_padding_mask=batch["token_pad"].to(device),
        )
        loss_total += float(token_cross_entropy(logits, targets, pad_id=loader.dataset.tokenizer.pad_id).item())
        texts, flags = model.greedy_decode(images, valid_mask=valid)
        pairs.extend(zip(batch["references"], texts))
        truncation.extend(flags)
    metrics = summarize(pairs, truncated=truncation)
    return {
        "loss": loss_total / max(1, batches),
        "exact_match": metrics.exact_match if pairs else None,
        "token_error_rate": metrics.token_error_rate if pairs else None,
        "render_success": metrics.render_success if pairs else None,
        "truncated_rate": metrics.truncated_rate if pairs else None,
        "count": len(pairs),
    }


def train(
    manifest: Path,
    output: Path,
    epochs: int = 1,
    coverage: bool = False,
    *,
    resume: Path | None = None,
    val_manifest: Path | None = None,
    batch_size: int = 2,
    accumulate: int = 1,
    workers: int = 0,
    learning_rate: float = 3e-4,
    device: str | None = None,
    amp: bool | None = None,
    patience: int = 5,
    max_val_batches: int | None = None,
) -> Path:
    """Train or resume a compact model and write a complete checkpoint."""
    if epochs <= 0 or batch_size <= 0 or accumulate <= 0:
        raise ValueError("epochs, batch_size, and accumulate must be positive")
    seed = configure_determinism()
    target_device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
    amp_enabled = bool(amp if amp is not None else target_device.type == "cuda") and target_device.type == "cuda"

    tokenizer = LatexTokenizer.default()
    records = load_formula_records(manifest, tokenizer=tokenizer)
    train_records = [record for record in records if record.split in {"train", "training"}]
    if not train_records:
        raise ValueError(
            "manifest has no training records; do not fall back to dev/test data"
        )
    validation_records = [record for record in records if record.split in {"dev", "val", "validation"}]
    if val_manifest is not None:
        validation_records = load_formula_records(val_manifest, tokenizer=tokenizer)
    fallback_config = CompactMathConfig(coverage=coverage)
    payload: dict[str, Any] | None = load_checkpoint(resume, map_location="cpu") if resume else None
    if payload is not None:
        tokenizer = _tokenizer_from_payload(payload)
        fallback_config = _config_from_payload(payload, fallback_config)
    config = fallback_config
    model = CompactMathModel(tokenizer=tokenizer, config=config).to(target_device)
    for record in records:
        token_count = len(tokenizer.encode(record.latex))
        if token_count > config.max_tokens:
            raise ValueError(
                f"sample {record.sample_id} has {token_count} tokens; "
                f"the configured max_tokens is {config.max_tokens}"
            )
    # The training loader is recreated with a seed derived from the epoch.  A
    # resumed run therefore sees exactly the same epoch order without relying
    # on private DataLoader-generator state.
    loader = _make_loader(train_records, tokenizer, config, batch_size=batch_size, shuffle=True, workers=workers, seed=seed)
    val_loader = _make_loader(validation_records, tokenizer, config, batch_size=batch_size, shuffle=False, workers=workers, seed=seed) if validation_records else None
    trainable = [parameter for parameter in model.parameters() if parameter.requires_grad]
    if not trainable:
        raise ValueError("model has no trainable parameters")
    optimizer = torch.optim.AdamW(trainable, lr=learning_rate)
    update_count = max(1, math.ceil(len(loader) / accumulate) * epochs)
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda step: max(0.0, 1.0 - step / max(1, update_count)))
    scaler = torch.cuda.amp.GradScaler(enabled=amp_enabled)
    start_epoch = 0
    best_metric = -float("inf")
    history: list[dict[str, Any]] = []
    if payload is not None:
        state = payload.get("model") or payload.get("state_dict")
        if not isinstance(state, dict):
            raise ValueError("resume checkpoint is missing model weights")
        model.load_state_dict(state)
        if isinstance(payload.get("optimizer"), dict):
            optimizer.load_state_dict(payload["optimizer"])
        if isinstance(payload.get("scheduler"), dict):
            scheduler.load_state_dict(payload["scheduler"])
        if isinstance(payload.get("scaler"), dict):
            scaler.load_state_dict(payload["scaler"])
        start_epoch = int(payload.get("epoch", -1)) + 1
        best_metric = float(payload.get("best_metric", best_metric))
        history = list(payload.get("history") or [])
        restore_rng_state(payload.get("rng_state"))

    no_improvement = 0
    for epoch in range(start_epoch, epochs):
        loader = _make_loader(
            train_records,
            tokenizer,
            config,
            batch_size=batch_size,
            shuffle=True,
            workers=workers,
            seed=seed + epoch,
        )
        model.train()
        batch_iterator = iter(loader)
        batch_index = -1
        while True:
            group: list[dict[str, Any]] = []
            for _ in range(accumulate):
                try:
                    group.append(next(batch_iterator))
                except StopIteration:
                    break
            if not group:
                break
            total_valid_tokens = sum(
                int((batch["decoder_tgt"] != tokenizer.pad_id).sum().item())
                for batch in group
            )
            total_valid_tokens = max(1, total_valid_tokens)
            optimizer.zero_grad(set_to_none=True)
            for batch in group:
                batch_index += 1
                images = batch["images"].to(target_device, non_blocking=True)
                valid = batch["valid"].to(target_device, non_blocking=True)
                with torch.autocast(device_type=target_device.type, enabled=amp_enabled):
                    logits = model(
                        images,
                        batch["decoder_in"].to(target_device, non_blocking=True),
                        valid_mask=valid,
                        token_padding_mask=batch["token_pad"].to(target_device, non_blocking=True),
                    )
                    # Normalize the accumulated gradient by the number of
                    # valid target tokens, not by microbatch count.  This is
                    # correct when labels have different lengths.
                    loss_sum = torch.nn.functional.cross_entropy(
                        logits.reshape(-1, logits.size(-1)),
                        batch["decoder_tgt"].to(target_device, non_blocking=True).reshape(-1),
                        ignore_index=tokenizer.pad_id,
                        reduction="sum",
                    )
                    loss = loss_sum / total_valid_tokens
                scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(optimizer)
            scaler.update()
            scheduler.step()
        validation = evaluate(model, val_loader, device=target_device, max_batches=max_val_batches) if val_loader else {"count": 0, "exact_match": None, "token_error_rate": None}
        metric = validation.get("exact_match")
        if metric is not None and float(metric) > best_metric:
            best_metric = float(metric)
            no_improvement = 0
        elif metric is not None:
            no_improvement += 1
        history.append({"epoch": epoch, "validation": validation, "learning_rate": scheduler.get_last_lr()[0]})
        save_checkpoint(output, {
            "checkpoint_schema_version": 2,
            "model": model.state_dict(),
            "config": config.__dict__,
            "tokenizer": tokenizer.token_to_id,
            "optimizer": optimizer.state_dict(),
            "scheduler": scheduler.state_dict(),
            "scaler": scaler.state_dict(),
            "rng_state": capture_rng_state(),
            "epoch": epoch,
            "batch_index": batch_index,
            "data_position": {"epoch": epoch, "batch_index": batch_index},
            "best_metric": best_metric,
            "history": history,
            "metadata": {
                "experiment": "E5-compact-coverage" if config.coverage else "E4-compact",
                "seed": seed,
                "device": str(target_device),
                "amp": amp_enabled,
                "dataset_digest": dataset_digest(records),
                "manifest": str(Path(manifest).resolve()),
                "validation_available": bool(validation_records),
                "train_count": len(train_records),
                "validation_count": len(validation_records),
                "epoch_seed": seed + epoch,
                "data_order": "deterministic-seed-per-epoch",
            },
        })
        if validation_records and no_improvement >= patience:
            break
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the compact math model")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--val-manifest", type=Path)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--accumulate", type=int, default=1)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--device")
    parser.add_argument("--amp", action="store_true")
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--patience", type=int, default=5)
    args = parser.parse_args(argv)
    try:
        path = train(args.manifest, args.output, epochs=args.epochs, coverage=args.coverage, resume=args.resume, val_manifest=args.val_manifest, batch_size=args.batch_size, accumulate=args.accumulate, workers=args.workers, learning_rate=args.learning_rate, device=args.device, amp=args.amp, patience=args.patience)
    except (UnsupportedLatexError, ValueError, FileNotFoundError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
