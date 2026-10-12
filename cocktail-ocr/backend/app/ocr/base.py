from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np


class EngineUnavailable(RuntimeError):
    pass


@dataclass
class OcrWord:
    text: str
    conf: float | None            # 0..1, None when the engine gives none
    poly: list[list[float]]       # 4 points, image coordinates
    start: int = 0                # char offset within the parent line text
    end: int = 0


@dataclass
class OcrLine:
    text: str
    conf: float | None
    poly: list[list[float]]
    # Word boxes when the engine reports them (exact); otherwise sub-boxes are
    # interpolated from character positions and marked approximate.
    words: list[OcrWord] = field(default_factory=list)


class OcrEngine(ABC):
    name: str = "base"
    #: Short human description stored with every pass.
    description: str = ""

    @abstractmethod
    def recognize(self, image: np.ndarray, mode: str = "auto") -> list[OcrLine]:
        """Run OCR on an RGB or grayscale uint8 array.

        ``mode`` is an engine-specific hint: "auto" (full page), "sparse"
        (scattered text) or "line" (a single cropped text line)."""

    def close(self) -> None:  # pragma: no cover - optional
        pass


def quad_from_bbox(x: float, y: float, w: float, h: float) -> list[list[float]]:
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def sub_quad(poly, a: float, b: float) -> list[list[float]]:
    """Portion [a, b] (fractions of the reading direction) of a text-line quad
    ordered TL, TR, BR, BL."""
    p = np.asarray(poly, dtype=float)
    tl, tr, br, bl = p[0], p[1], p[2], p[3]
    top_a, top_b = tl + (tr - tl) * a, tl + (tr - tl) * b
    bot_a, bot_b = bl + (br - bl) * a, bl + (br - bl) * b
    return [top_a.tolist(), top_b.tolist(), bot_b.tolist(), bot_a.tolist()]
