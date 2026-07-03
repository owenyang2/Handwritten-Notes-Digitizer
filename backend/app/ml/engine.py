import abc
import torch
import numpy as np
from PIL import Image as PILImage
import logging

from app.ml.htr_model import CRNN
from app.ml.decode import beam_search_decode, _prepare_crop
from app.ml.htr_config import ALPHABET, BLANK_INDEX

logger = logging.getLogger(__name__)

class HTREngine(abc.ABC):
    """Abstract interface for handwritten text recognition engines."""

    @abc.abstractmethod
    def recognize_crop(self, crop: np.ndarray) -> tuple[str, float]:
        """Recognize text from a single crop image.
        
        Parameters
        ----------
        crop:
            A 2D numpy array representing the preprocessed (grayscale) image crop.
            Values are expected to be normalized floats in [0, 1].
            
        Returns
        -------
        text:
            The recognized string.
        confidence:
            A confidence score between 0.0 and 1.0.
        """
        pass


def _native_beam_search_decode(
    log_probs: torch.Tensor,
    blank_index: int = BLANK_INDEX,
    beam_width: int = 5
) -> tuple[str, float]:
    """A native Python beam search decoder without an external language model.
    
    Returns the decoded string and the confidence score.
    """
    # log_probs shape: (T, C)
    T, C = log_probs.shape
    
    # beam element: (prefix_str, last_char, log_prob)
    # Start with empty string, no last char (blank), and log prob 0.0
    beams = [("", None, 0.0)]
    
    for t in range(T):
        step_log_probs = log_probs[t].tolist()
        new_beams = []
        
        for prefix, last_char, path_prob in beams:
            for c in range(C):
                new_prob = path_prob + step_log_probs[c]
                
                if c == blank_index:
                    new_beams.append((prefix, None, new_prob))
                else:
                    char = ALPHABET[c - 1] if 0 < c <= len(ALPHABET) else ""
                    # Collapse consecutive duplicates if no blank in between
                    if char == last_char:
                        new_beams.append((prefix, char, new_prob))
                    else:
                        new_beams.append((prefix + char, char, new_prob))
                        
        # Sort and prune
        # Merge beams with identical prefixes
        merged = {}
        for prefix, last_char, prob in new_beams:
            key = (prefix, last_char)
            if key not in merged or prob > merged[key]:
                merged[key] = prob
                
        sorted_beams = sorted(
            [(k[0], k[1], v) for k, v in merged.items()],
            key=lambda x: x[2],
            reverse=True
        )
        beams = sorted_beams[:beam_width]

    if not beams:
        return "", 0.0
        
    best_text, _, best_log_prob = beams[0]
    # Simple confidence approximation: average log-prob per character (if non-empty)
    conf = float(np.exp(best_log_prob / max(len(best_text), 1))) if best_text else 0.0
    return best_text, conf


class CRNNEngine(HTREngine):
    """The default engine using the custom CRNN with CTC decoding."""

    def __init__(self, model: CRNN, use_beam_search: bool = False):
        self.model = model
        self.use_beam_search = use_beam_search
        self.model.eval()

    def recognize_crop(self, crop: np.ndarray) -> tuple[str, float]:
        target_height = self.model.config.image_height
        tensor = _prepare_crop(crop, target_height)

        with torch.no_grad():
            log_probs = self.model(tensor)  # (T, 1, C)

        log_probs_single = log_probs.squeeze(1)
        
        if self.use_beam_search:
            return _native_beam_search_decode(log_probs_single, blank_index=self.model.config.blank_index)
        else:
            return beam_search_decode(log_probs_single, beam_width=10)


class TrOCREngine(HTREngine):
    """An engine utilizing Hugging Face's TrOCR model."""

    def __init__(self, model_name: str = "microsoft/trocr-base-handwritten"):
        logger.info(f"Loading TrOCR model: {model_name} (this may take a while to download weights)...")
        from transformers import VisionEncoderDecoderModel, TrOCRProcessor
        self.processor = TrOCRProcessor.from_pretrained(model_name)
        self.model = VisionEncoderDecoderModel.from_pretrained(model_name)
        self.model.eval()

    def recognize_crop(self, crop: np.ndarray) -> tuple[str, float]:
        # Convert float normalized array [0,1] to uint8 [0, 255] RGB PIL Image
        # TrOCR expects RGB images.
        uint8_crop = (crop * 255.0).clip(0, 255).astype(np.uint8)
        pil_image = PILImage.fromarray(uint8_crop, mode="L").convert("RGB")
        
        # Processor handles resizing and normalization for TrOCR
        pixel_values = self.processor(pil_image, return_tensors="pt").pixel_values
        
        with torch.no_grad():
            outputs = self.model.generate(
                pixel_values, 
                return_dict_in_generate=True, 
                output_scores=True,
                max_length=64
            )
            
        generated_ids = outputs.sequences
        text = self.processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
        
        confidence = 0.9
        
        if hasattr(outputs, "sequences_scores") and outputs.sequences_scores is not None:
            confidence = float(torch.exp(outputs.sequences_scores[0]))
            
        return text.strip(), confidence
