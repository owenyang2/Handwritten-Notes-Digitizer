# Math-notes benchmark runner

The benchmark CLI consumes a verified JSONL prediction file. Each row must
contain `reference` and `prediction`; optional fields are `sample_id`,
`region_id`, `predicted_region_id`, `page_id`, `truncated`, `missed`,
`page_error`, `latency_ms`, and `peak_memory_bytes` (or the legacy
`memory_bytes` name).

```bash
PYTHONPATH=backend backend/.venv/bin/python -m benchmarks.runner \
  --predictions results/formula-test.predictions.jsonl \
  --output results/formula-test.report.json \
  --version 1.0.0 --split test
```

The report includes normalized exact match, token error rate, renderability,
truncation rate, critical-structure error rate, and page-level layout/order /
end-to-end summaries. Blank, unreadable, failed, and truncated samples remain
in the denominator. Use a fixed development split for selection and reserve a
separate final test split for release claims.

For personal-note comparisons, pass page identifiers to
`paired_bootstrap_delta_ci` in `benchmarks.metrics`. The page is the sampling
unit so a page with many crops cannot dominate uncertainty estimates.

The repository intentionally ships no licensed handwriting pixels. A report
cannot be a real accuracy result until the user supplies a verified image/
label manifest and records its dataset/version/hash provenance.
