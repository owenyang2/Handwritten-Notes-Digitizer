"""Download the public pretrained prose model at an immutable revision."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.ml.artifacts import sha256_tree


def main():
    from huggingface_hub import HfApi, snapshot_download
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, default=Path("backend/var/objectstore/models/trocr_handwritten"))
    parser.add_argument("--revision", default="main")
    args = parser.parse_args()
    model_id = "microsoft/trocr-base-handwritten"
    revision = HfApi().model_info(model_id, revision=args.revision).sha
    snapshot_download(model_id, revision=revision, local_dir=str(args.destination), allow_patterns=[
        "*.json", "*.txt", "model.safetensors", "README.md", "LICENSE*",
    ])
    manifest = {"backend_id": "trocr-handwritten", "model_id": model_id,
                "revision": revision, "source_url": f"https://huggingface.co/{model_id}",
                "weight_sha256": sha256_tree(args.destination), "license": "MIT",
                "supported_inputs": ["prose_line"]}
    (args.destination / "model_artifact.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
