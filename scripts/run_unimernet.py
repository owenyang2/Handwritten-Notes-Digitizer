"""Offline JSON-line inference worker for the official UniMERNet-small weights.

Run with the isolated upstream environment. The API's pinned Transformers
environment communicates with this process without importing upstream packages.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    args = parser.parse_args()
    output = sys.stdout
    # Upstream constructors print diagnostics. Keep the protocol on stdout.
    with contextlib.redirect_stdout(sys.stderr):
        try:
            import torch
            from PIL import Image
            from omegaconf import OmegaConf
            from unimernet.models.unimernet.unimernet import UniMERModel
            from unimernet.processors.formula_processor import FormulaImageEvalProcessor

            torch.set_num_threads(4)
            root = args.model_dir.resolve()
            model = UniMERModel(
                model_name=None,
                model_config=OmegaConf.create({"model_name": str(root), "max_seq_len": 160}),
                tokenizer_name=None,
                tokenizer_config=OmegaConf.create({"path": str(root)}),
            )
            checkpoint = torch.load(root / "unimernet_small.pth", map_location="cpu", weights_only=True)
            model.load_state_dict(checkpoint["model"], strict=True)
            model.float().eval()
            processor = FormulaImageEvalProcessor(image_size=[192, 672])

            def pixel_values(image):
                tensor = processor(image).unsqueeze(0)
                return tensor.repeat(1, 3, 1, 1) if tensor.shape[1] == 1 else tensor

            with torch.inference_mode():
                probe = model.model.model.generate(
                    pixel_values=pixel_values(Image.new("RGB", (96, 48), "white")),
                    max_new_tokens=1,
                    do_sample=False,
                    decoder_start_token_id=model.tokenizer.bos_token_id,
                )
            if probe.ndim != 2 or probe.shape[-1] < 2:
                raise RuntimeError("Model inference probe returned invalid tokens")
        except Exception as exc:
            print(json.dumps({"error": str(exc)}), file=output, flush=True)
            return 1
        print(json.dumps({"ready": True}), file=output, flush=True)
        for line in sys.stdin:
            try:
                request = json.loads(line)
                limit = max(1, min(int(request.get("max_tokens", 160)), 1536))
                beams = max(1, min(int(request.get("beam_size", 1)), 8))
                with Image.open(io.BytesIO(base64.b64decode(request["image"], validate=True))) as opened:
                    image = opened.convert("RGB")
                with torch.inference_mode():
                    tokens = model.model.model.generate(
                        pixel_values=pixel_values(image),
                        max_new_tokens=limit,
                        num_beams=beams,
                        do_sample=False,
                        decoder_start_token_id=model.tokenizer.bos_token_id,
                    )
                text = model.tokenizer.token2str(tokens[:, 1:])[0]
                truncated = tokens.shape[-1] >= limit + 1 and int(tokens[0, -1]) != model.tokenizer.eos_token_id
                response = {"text": text, "truncated": truncated, "token_count": int(tokens.shape[-1] - 1)}
            except Exception as exc:
                response = {"error": str(exc)}
            print(json.dumps(response), file=output, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
