"""Task-specific prose routing, independent of the CRNN personalization capability."""
from app.ml.backends.base import EngineCandidate, EngineResult
from app.ml.backends.trocr import TrOCREngine
from app.ml.recognition import get_loaded_model_status, get_base_model, recognize_crop


def get_prose_engine_status():
    status = get_loaded_model_status()
    if status.loaded:
        return {"loaded": True, "backend": status.backend, "personalization_compatible": True}
    engine = TrOCREngine()
    available = engine.is_available()
    return {"loaded": available, "backend": engine.engine_id,
            "personalization_compatible": False, "reason": None if available else engine._reason,
            "provenance": engine._provenance}


def recognize_prose_crop(image_bytes):
    if get_loaded_model_status().loaded:
        text = recognize_crop(image_bytes, get_base_model())
        return EngineResult(engine_id="crnn-ctc", engine_version="trained", candidates=[EngineCandidate(text=text)])
    return TrOCREngine().recognize_crop(image_bytes)
