# Math-notes data card

## Supported records

`training.datasets.load_formula_records` reads JSONL records with:

```json
{"sample_id":"mw-0001", "image":"crops/mw-0001.png", "inkml":"optional.inkml", "rawLabel":"x^2", "normalizedLabel":"x^2", "split":"train", "source_document_id":"page-group-1", "page_id":"page-1"}
```

Every record has one stable image/InkML source and one strict normalized LaTeX
label. Unsupported commands fail validation instead of being dropped. `rawLabel`
is retained for auditing. MathWriting InkML is rasterized deterministically;
the loader preserves aspect ratio and emits an explicit valid-pixel mask.

## Split policy

Use official public dataset splits where available. A source document/page group
must not appear in more than one split. For personal notes, create the split at
whole-document level before making crops. Keep development labels separate from
the final test labels, and never use the general guardrail set for training.

The manifest digest includes sample ids, labels, split/source metadata, and
source-file bytes. Keep the manifest, dataset version/license, and digest with
each checkpoint.

## Personal data and consent

The web app keeps original uploads and immutable review revisions. A crop enters
the new formula training set only when the reviewer explicitly checks consent;
excluded, blank, stale, or hashless crops are ineligible. The object-store path
and SHA-256 are tied to the reviewed revision so a later crop edit cannot change
what an earlier training run saw.

The repository contains no user notes or licensed pixels. Install datasets from
their own terms and do not commit private notes, credentials, or generated
artifacts.
