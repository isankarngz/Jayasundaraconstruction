"""Tesseract 5 (LSTM) via its command-line interface.  A materially different
detector/recogniser from PaddleOCR, used as the independent second engine.
Gives true word-level boxes and confidences."""
from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import tempfile

import cv2
import numpy as np

from .base import EngineUnavailable, OcrEngine, OcrLine, OcrWord, quad_from_bbox

_PSM = {"auto": "3", "sparse": "11", "line": "7", "block": "6"}


class TesseractEngine(OcrEngine):
    name = "tesseract-5"
    description = "Tesseract LSTM OCR (eng), word-level TSV output"

    def __init__(self, cmd: str = "tesseract", timeout: int = 120, lang: str = "eng"):
        path = shutil.which(cmd)
        if not path:
            raise EngineUnavailable(f"tesseract executable '{cmd}' not found on PATH")
        self.cmd = path
        self.timeout = timeout
        self.lang = lang
        try:
            out = subprocess.run([self.cmd, "--version"], capture_output=True, text=True, timeout=20)
            first = (out.stdout or out.stderr).splitlines()[0] if (out.stdout or out.stderr) else ""
            self.name = "tesseract-" + (first.split()[-1] if first else "5")
        except Exception:  # pragma: no cover
            pass

    def recognize(self, image: np.ndarray, mode: str = "auto") -> list[OcrLine]:
        psm = _PSM.get(mode, "3")
        img = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            cv2.imwrite(path, img)
            env = dict(os.environ, OMP_THREAD_LIMIT="1")
            proc = subprocess.run(
                [self.cmd, path, "stdout", "--psm", psm, "-l", self.lang, "tsv"],
                capture_output=True, text=True, timeout=self.timeout, env=env)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        if proc.returncode != 0:
            raise RuntimeError(f"tesseract exited {proc.returncode}: {proc.stderr.strip()[:300]}")
        return parse_tsv(proc.stdout)


def parse_tsv(tsv: str) -> list[OcrLine]:
    groups: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        if row.get("level") != "5":
            continue
        text = (row.get("text") or "").strip()
        if not text:
            continue
        key = (row["page_num"], row["block_num"], row["par_num"], row["line_num"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
    lines: list[OcrLine] = []
    for key in order:
        words: list[OcrWord] = []
        parts: list[str] = []
        pos = 0
        for r in groups[key]:
            t = r["text"].strip()
            x, y, w, h = (float(r[k]) for k in ("left", "top", "width", "height"))
            c = float(r.get("conf", -1))
            conf = None if c < 0 else c / 100.0
            if parts:
                pos += 1
            words.append(OcrWord(t, conf, quad_from_bbox(x, y, w, h), pos, pos + len(t)))
            parts.append(t)
            pos += len(t)
        xs = [p[0] for w in words for p in w.poly]
        ys = [p[1] for w in words for p in w.poly]
        confs = [w.conf for w in words if w.conf is not None]
        lines.append(OcrLine(
            text=" ".join(parts),
            conf=(sum(confs) / len(confs)) if confs else None,
            poly=quad_from_bbox(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)),
            words=words))
    return lines
