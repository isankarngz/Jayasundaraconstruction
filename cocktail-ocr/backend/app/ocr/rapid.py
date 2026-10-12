"""PaddleOCR PP-OCRv3 detection + recognition models, executed locally through
ONNX Runtime (RapidOCR).  The models ship inside the Python wheel, so no
network access or model download is needed at runtime."""
from __future__ import annotations

import os

import numpy as np

from .base import EngineUnavailable, OcrEngine, OcrLine


class RapidPaddleEngine(OcrEngine):
    name = "paddleocr-ppocrv3"
    description = "PaddleOCR PP-OCRv3 det/cls/rec via ONNX Runtime (RapidOCR)"

    def __init__(self, threads: int = 0):
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception as exc:  # pragma: no cover
            raise EngineUnavailable(f"rapidocr_onnxruntime not importable: {exc}") from exc
        if threads:
            os.environ.setdefault("OMP_NUM_THREADS", str(threads))
        # text_score is lowered so weak readings reach our own decision logic
        # instead of being silently dropped inside the engine.
        kwargs = dict(text_score=0.05, width_height_ratio=-1, min_height=8,
                      box_thresh=0.35, unclip_ratio=1.7)
        try:
            self._ocr = RapidOCR(**kwargs)
        except TypeError:  # older/newer releases with a different signature
            self._ocr = RapidOCR()
        # Mirror the documented defaults where the constructor ignored kwargs.
        self._ocr.text_score = 0.05
        self._ocr.width_height_ratio = -1
        self._ocr.min_height = 8
        try:
            pp = self._ocr.text_detector.postprocess_op
            pp.box_thresh = 0.35
            pp.unclip_ratio = 1.7
        except AttributeError:  # pragma: no cover
            pass

    def recognize(self, image: np.ndarray, mode: str = "auto") -> list[OcrLine]:
        img = image
        if img.ndim == 2:
            img = np.stack([img] * 3, axis=-1)
        # RapidOCR expects BGR like OpenCV; channel order matters little for
        # text but keep it correct.
        img = np.ascontiguousarray(img[:, :, ::-1])
        result, _ = self._ocr(img)
        lines: list[OcrLine] = []
        for poly, text, score in result or []:
            try:
                conf = float(score)
            except (TypeError, ValueError):
                conf = None
            lines.append(OcrLine(text=str(text), conf=conf,
                                 poly=[[float(x), float(y)] for x, y in poly]))
        return lines
