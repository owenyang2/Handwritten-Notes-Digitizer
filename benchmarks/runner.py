"""CLI-friendly benchmark runner for verified formula manifests."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.metrics import ExpressionMetrics, PageMetrics, summarize, summarize_page_layout


@dataclass(frozen=True)
class BenchmarkReport:
    benchmark_version: str
    split: str
    expressions: ExpressionMetrics
    pages: PageMetrics
    provenance: dict[str, Any]
    runtime: "RuntimeMetrics" = field(default_factory=lambda: RuntimeMetrics())


@dataclass(frozen=True)
class RuntimeMetrics:
    """Operational metrics kept separate from transcription quality."""

    p50_latency_ms: float | None = None
    p95_latency_ms: float | None = None
    peak_memory_bytes: int | None = None
    sample_count: int = 0


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("values must not be empty")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def load_prediction_records(path: str | Path) -> list[dict[str, Any]]:
    """Load JSONL predictions and reject incomplete evaluation rows."""
    records: list[dict[str, Any]] = []
    for index, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError(f"prediction row {index} is not an object")
        for key in ("reference", "prediction"):
            if not isinstance(item.get(key), str):
                raise ValueError(f"prediction row {index} is missing string field {key!r}")
        records.append(item)
    return records


def evaluate_records(records: list[dict[str, Any]], *, version: str = "1.0", split: str = "test") -> BenchmarkReport:
    pairs = [(str(item["reference"]), str(item["prediction"])) for item in records]
    truncated = [bool(item.get("truncated", False)) for item in records]
    pages: dict[str, dict[str, object]] = {}
    for index, item in enumerate(records):
        page_id = str(item.get("page_id", f"row-{index}"))
        page = pages.setdefault(page_id, {"expected": [], "predicted": [], "correct": True})
        expected_id = str(item.get("region_id", item.get("sample_id", index)))
        page["expected"].append(expected_id)
        predicted_id = item.get("predicted_region_id")
        if predicted_id is None:
            predicted_id = expected_id if not item.get("missed", False) else f"missing-{expected_id}"
        page["predicted"].append(str(predicted_id))
        page["correct"] = bool(page["correct"]) and not bool(item.get("page_error", False))
    latency = [float(item["latency_ms"]) for item in records if item.get("latency_ms") is not None]
    memory_values: list[int] = []
    for item in records:
        raw_memory = item.get("peak_memory_bytes")
        if raw_memory is None:
            raw_memory = item.get("memory_bytes")
        if raw_memory is not None:
            memory_values.append(int(raw_memory))
    return BenchmarkReport(
        benchmark_version=version,
        split=split,
        expressions=summarize(pairs, truncated=truncated),
        pages=summarize_page_layout(list(pages.values())),
        provenance={
            "record_count": len(records),
            "truncation_in_denominator": True,
            "latency_samples": len(latency),
            "memory_samples": len(memory_values),
        },
        runtime=RuntimeMetrics(
            p50_latency_ms=_percentile(latency, 0.50) if latency else None,
            p95_latency_ms=_percentile(latency, 0.95) if latency else None,
            peak_memory_bytes=max(memory_values) if memory_values else None,
            sample_count=len(latency),
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate verified formula predictions")
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--version", default="1.0")
    parser.add_argument("--split", default="test")
    args = parser.parse_args(argv)
    report = evaluate_records(load_prediction_records(args.predictions), version=args.version, split=args.split)
    encoded = json.dumps(asdict(report), indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
