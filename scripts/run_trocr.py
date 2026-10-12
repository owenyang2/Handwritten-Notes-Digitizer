"""Bounded offline JSON-line worker for Microsoft's pretrained prose recognizer."""
import argparse
import base64
import contextlib
import io
import json
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    args = parser.parse_args()
    output = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        try:
            import torch
            from PIL import Image
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel
            torch.set_num_threads(4)
            processor = TrOCRProcessor.from_pretrained(args.model_dir, local_files_only=True)
            model, loading = VisionEncoderDecoderModel.from_pretrained(args.model_dir, local_files_only=True, use_safetensors=True, output_loading_info=True)
            # BEiT's unused pooler is absent from the official checkpoint.
            # Every tensor used for recognition must come from trained weights.
            allowed_missing = {"encoder.pooler.dense.bias", "encoder.pooler.dense.weight"}
            if set(loading.get("missing_keys", [])) - allowed_missing or loading.get("mismatched_keys") or loading.get("error_msgs"):
                raise ValueError("Incomplete or incompatible trained TrOCR checkpoint")
            model.eval()
            with torch.inference_mode():
                probe = model.generate(processor(Image.new("RGB", (96, 48), "white"), return_tensors="pt").pixel_values, max_new_tokens=1)
            if probe.ndim != 2:
                raise ValueError("Invalid inference probe")
        except Exception as exc:
            print(json.dumps({"error": str(exc)}), file=output, flush=True)
            return 1
        print(json.dumps({"ready": True}), file=output, flush=True)
        for line in sys.stdin:
            try:
                request = json.loads(line)
                limit = max(1, min(256, int(request.get("max_tokens", 160))))
                with Image.open(io.BytesIO(base64.b64decode(request["image"], validate=True))) as source:
                    pixels = processor(source.convert("RGB"), return_tensors="pt").pixel_values
                with torch.inference_mode():
                    tokens = model.generate(pixels, max_new_tokens=limit, num_beams=max(1, min(8, int(request.get("beam_size", 1)))), do_sample=False, use_cache=True)
                response = {"text": processor.batch_decode(tokens, skip_special_tokens=True)[0].strip(),
                            "truncated": tokens.shape[-1] >= limit+1 and int(tokens[0, -1]) != model.config.eos_token_id}
            except Exception as exc:
                response = {"error": str(exc)}
            print(json.dumps(response), file=output, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
