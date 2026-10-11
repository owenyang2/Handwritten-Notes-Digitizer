"""CRNN crop recognizer using the shared CTC decoder."""

import numpy as np
import torch

from app.ml.decode import _prepare_crop, beam_search_decode
from app.ml.htr_model import CRNN


class CRNNEngine:
    """The default engine using the custom CRNN with CTC decoding."""

    def __init__(self, model: CRNN):
        self.model = model
        self.model.eval()

    def recognize_crop(self, crop: np.ndarray) -> tuple[str, float]:
        tensor = _prepare_crop(crop, self.model.config.image_height)
        with torch.no_grad():
            log_probs = self.model(tensor)
        return beam_search_decode(log_probs.squeeze(1), beam_width=10)
