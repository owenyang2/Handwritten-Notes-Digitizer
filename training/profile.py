"""Small, repeatable profiler for compact formula-model pilots.

This intentionally reports operational facts separately from accuracy: warmed
decoder latency, image-padding overhead, CPU data time, and peak CUDA memory.
It can be imported by a notebook or used as a smoke test without requiring a
full benchmark suite.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import torch


@dataclass(frozen=True)
class ProfileResult:
    device: str
    measured_batches: int
    mean_forward_ms: float
    mean_decode_ms: float
    padding_overhead: float
    mean_data_wait_ms: float
    peak_cuda_memory_bytes: int | None


def _batch_padding_ratio(batch: dict[str, Any]) -> float:
    valid = batch["valid"]
    padded = int(valid.numel())
    useful = int(valid.sum().item())
    if padded == 0:
        return 0.0
    return 1.0 - useful / padded


@torch.no_grad()
def profile_model(
    model: torch.nn.Module,
    batches: Iterable[dict[str, Any]],
    *,
    device: str | torch.device = "cpu",
    warmup: int = 1,
    steps: int = 20,
) -> ProfileResult:
    """Profile a model over collated batches returned by ``training.cli``."""
    if warmup < 0 or steps <= 0:
        raise ValueError("warmup must be non-negative and steps must be positive")
    target = torch.device(device)
    model = model.to(target).eval()
    materialized = list(batches)
    if not materialized:
        raise ValueError("at least one batch is required")
    if target.type == "cuda":
        torch.cuda.reset_peak_memory_stats(target)

    iterator = iter(materialized)
    for _ in range(warmup):
        batch = next(iterator, materialized[0])
        images = batch["images"].to(target)
        model(images, batch["decoder_in"].to(target), valid_mask=batch["valid"].to(target), token_padding_mask=batch["token_pad"].to(target))
    if target.type == "cuda":
        torch.cuda.synchronize(target)

    forward_times: list[float] = []
    decode_times: list[float] = []
    padding: list[float] = []
    data_wait: list[float] = []
    last_end = time.perf_counter()
    for index in range(steps):
        data_started = time.perf_counter()
        batch = materialized[index % len(materialized)]
        data_wait.append((data_started - last_end) * 1000)
        images = batch["images"].to(target)
        decoder_in = batch["decoder_in"].to(target)
        valid = batch["valid"].to(target)
        padding.append(_batch_padding_ratio(batch))
        started = time.perf_counter()
        model(images, decoder_in, valid_mask=valid, token_padding_mask=batch["token_pad"].to(target))
        if target.type == "cuda":
            torch.cuda.synchronize(target)
        forward_times.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        decode = getattr(model, "greedy_decode", None)
        if callable(decode):
            decode(images, valid_mask=valid)
        if target.type == "cuda":
            torch.cuda.synchronize(target)
        decode_times.append((time.perf_counter() - started) * 1000)
        last_end = time.perf_counter()

    peak = torch.cuda.max_memory_allocated(target) if target.type == "cuda" else None
    return ProfileResult(
        device=str(target),
        measured_batches=steps,
        mean_forward_ms=sum(forward_times) / len(forward_times),
        mean_decode_ms=sum(decode_times) / len(decode_times),
        padding_overhead=sum(padding) / len(padding),
        mean_data_wait_ms=sum(data_wait) / len(data_wait),
        peak_cuda_memory_bytes=int(peak) if peak is not None else None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Profile a compact math model from a checkpoint")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--steps", type=int, default=20)
    args = parser.parse_args(argv)
    from app.ml.math.model import CompactMathConfig, CompactMathModel
    from training.cli import _make_loader, _tokenizer_from_payload
    from training.datasets import load_formula_records

    payload = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    if not isinstance(payload, dict):
        raise SystemExit("checkpoint must contain a mapping")
    config = CompactMathConfig(**{key: value for key, value in (payload.get("config") or {}).items() if key in CompactMathConfig.__dataclass_fields__})
    tokenizer = _tokenizer_from_payload(payload)
    model = CompactMathModel(tokenizer=tokenizer, config=config)
    state = payload.get("model") or payload.get("state_dict")
    if not isinstance(state, dict):
        raise SystemExit("checkpoint has no model weights")
    model.load_state_dict(state)
    manifest = Path(payload.get("metadata", {}).get("manifest", ""))
    if not manifest.is_file():
        raise SystemExit("checkpoint metadata does not contain a usable manifest path; use profile_model() from a notebook")
    records = load_formula_records(manifest, tokenizer=tokenizer)
    loader = _make_loader(records, tokenizer, config, batch_size=2, shuffle=False, workers=0, seed=1337)
    print(json.dumps(asdict(profile_model(model, loader, device=args.device, steps=args.steps)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
