"""Per-thread engine instances (ONNX sessions / temp files are not shared
between worker threads)."""
from __future__ import annotations

import logging
import threading

from ..config import settings
from .base import EngineUnavailable, OcrEngine

log = logging.getLogger(__name__)
_local = threading.local()


def primary_engine() -> OcrEngine:
    eng = getattr(_local, "primary", None)
    if eng is None:
        from .rapid import RapidPaddleEngine
        eng = RapidPaddleEngine(threads=settings.ocr_threads)
        _local.primary = eng
    return eng


def secondary_engine() -> OcrEngine | None:
    """Returns None when disabled; raises EngineUnavailable when enabled but
    missing, so the caller can mark images for review rather than negative."""
    if not settings.enable_tesseract:
        return None
    eng = getattr(_local, "secondary", None)
    if eng is None:
        from .tesseract import TesseractEngine
        eng = TesseractEngine(settings.tesseract_cmd, settings.tesseract_timeout)
        _local.secondary = eng
    return eng


def engine_status() -> dict:
    status = {}
    try:
        e = primary_engine()
        status["primary"] = {"name": e.name, "available": True, "description": e.description}
    except Exception as exc:  # pragma: no cover
        status["primary"] = {"name": "paddleocr-ppocrv3", "available": False, "error": str(exc)}
    if not settings.enable_tesseract:
        status["secondary"] = {"name": "tesseract", "available": False, "error": "disabled by CF_ENABLE_TESSERACT=0"}
    else:
        try:
            e2 = secondary_engine()
            status["secondary"] = {"name": e2.name, "available": True, "description": e2.description}
        except EngineUnavailable as exc:
            status["secondary"] = {"name": "tesseract", "available": False, "error": str(exc)}
    return status
