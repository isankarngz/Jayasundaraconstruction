"""Multi-stage OCR pipeline and the automated decision policy.

Stage A  primary engine (PaddleOCR PP-OCRv3) on the original image.
Stage B  primary engine on preprocessing variants: sideways rotations,
         upscaling / upscaled tiles, CLAHE, binarisation, inversion,
         denoise+sharpen, contrast stretch, 180 degree rotation.
Stage R  region re-OCR: every fuzzy candidate, every low-confidence possible
         match, and every low-confidence text line is cropped, upscaled and
         re-read by BOTH engines.
Stage C  independent second engine (Tesseract) on the original, a binarised
         upscale and both sideways rotations.

A confident EXACT/SPACED hit ends the run early (unless the job is in
"thorough" mode).  Anything that cannot be positively resolved - failed
passes, missing second engine, fuzzy candidates, low-confidence possible
matches, cross-engine disagreements, very blurred or tiny images,
multi-page files, unreadable text regions - yields REVIEW, never NO MATCH.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Callable

import numpy as np

from . import imaging
from .config import settings
from .matching import (CANDIDATE_KINDS, EXCLUDED, MATCH_KINDS, Hit, find_hits)
from .ocr.base import EngineUnavailable, OcrEngine, OcrLine, sub_quad

MATCH = "match"
NO_MATCH = "no_match"
REVIEW = "review"


@dataclass
class Options:
    fuzzy: bool = True           # flag OCR-confusion / near-miss / merged-word candidates
    thorough: bool = False       # run every pass even after a confident match
    match_confidence: float = field(default_factory=lambda: settings.match_confidence)
    low_confidence: float = field(default_factory=lambda: settings.low_confidence)
    blur_threshold: float = field(default_factory=lambda: settings.blur_threshold)
    max_regions: int = 40

    @classmethod
    def from_job(cls, job_settings: dict) -> "Options":
        o = cls()
        o.fuzzy = bool(job_settings.get("fuzzy", True))
        o.thorough = bool(job_settings.get("thorough", False))
        return o


@dataclass
class PassRecord:
    seq: int
    stage: str
    engine: str
    variant: str
    description: str
    duration_ms: int
    status: str
    error: str | None
    lines: list[dict]

    @property
    def text(self) -> str:
        return "\n".join(l["text"] for l in self.lines)

    @property
    def mean_conf(self) -> float | None:
        c = [l["conf"] for l in self.lines if l["conf"] is not None]
        return sum(c) / len(c) if c else None


@dataclass
class HitRecord:
    kind: str
    target: str
    raw: str
    normalized: str
    confidence: float | None
    poly: list[list[float]]
    approx: bool
    line_text: str
    engine: str
    variant: str
    pass_seq: int
    occurrence: int | None = None


@dataclass
class Analysis:
    status: str
    reasons: list[str]
    warnings: list[str]
    width: int
    height: int
    format: str
    best_confidence: float | None
    occurrences: int
    words_found: list[str]
    word_counts: dict[str, int]
    passes: list[PassRecord]
    hits: list[HitRecord]
    sharpness: float

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------- geometry utils --

def _bbox(poly) -> tuple[float, float, float, float]:
    p = np.asarray(poly, dtype=float)
    return float(p[:, 0].min()), float(p[:, 1].min()), float(p[:, 0].max()), float(p[:, 1].max())


def _overlap(a, b) -> float:
    """Intersection over the smaller box's area."""
    ax0, ay0, ax1, ay1 = _bbox(a)
    bx0, by0, bx1, by1 = _bbox(b)
    iw = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    ih = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    small = min(max(1e-6, (ax1 - ax0) * (ay1 - ay0)), max(1e-6, (bx1 - bx0) * (by1 - by0)))
    return inter / small


def _round_poly(p) -> list[list[float]]:
    return [[round(float(x), 1), round(float(y), 1)] for x, y in np.asarray(p, dtype=float)]


# --------------------------------------------------------------- analysis --

class _Run:
    def __init__(self, loaded: imaging.LoadedImage, opts: Options, primary: OcrEngine,
                 secondary: OcrEngine | None, secondary_error: str | None,
                 should_stop: Callable[[], bool] | None):
        self.img = loaded
        self.opts = opts
        self.primary = primary
        self.secondary = secondary
        self.secondary_error = secondary_error
        self.should_stop = should_stop or (lambda: False)
        self.passes: list[PassRecord] = []
        self.hits: list[HitRecord] = []
        self.original_lines: list[dict] = []

    # Run one OCR pass and record lines + hits in original coordinates.
    def run(self, stage: str, engine: OcrEngine, variant: imaging.Variant, mode: str = "auto") -> list[dict] | None:
        if self.should_stop():
            raise InterruptedError("processing interrupted")
        seq = len(self.passes) + 1
        t0 = time.perf_counter()
        try:
            lines: list[OcrLine] = engine.recognize(variant.image, mode)
        except Exception as exc:  # recorded, escalates the image to review
            self.passes.append(PassRecord(seq, stage, engine.name, variant.name, variant.description,
                                          int((time.perf_counter() - t0) * 1000), "error",
                                          f"{type(exc).__name__}: {exc}"[:500], []))
            return None
        dur = int((time.perf_counter() - t0) * 1000)
        mapped: list[dict] = []
        for ln in lines:
            line_poly = variant.to_orig(np.asarray(ln.poly, dtype=float))
            mapped.append({"text": ln.text, "conf": None if ln.conf is None else round(ln.conf, 4),
                           "poly": _round_poly(line_poly)})
            for h in find_hits(ln.text, fuzzy=self.opts.fuzzy):
                self.hits.append(self._hit_record(h, ln, variant, engine.name, seq))
        self.passes.append(PassRecord(seq, stage, engine.name, variant.name, variant.description,
                                      dur, "ok", None, mapped))
        return mapped

    @staticmethod
    def _hit_record(h: Hit, ln: OcrLine, variant: imaging.Variant, engine: str, seq: int) -> HitRecord:
        conf = ln.conf
        if ln.words:
            ws = [w for w in ln.words if w.start < h.end and h.start < w.end]
            if ws:
                pts = np.concatenate([variant.to_orig(np.asarray(w.poly, dtype=float)) for w in ws])
                x0, y0 = pts.min(axis=0)
                x1, y1 = pts.max(axis=0)
                poly = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
                wc = [w.conf for w in ws if w.conf is not None]
                conf = min(wc) if wc else conf
                return HitRecord(h.kind, h.target, h.raw, h.normalized, conf, _round_poly(poly), False,
                                 ln.text, engine, variant.name, seq)
        n = max(1, len(ln.text))
        q = sub_quad(ln.poly, h.start / n, h.end / n)
        poly = variant.to_orig(np.asarray(q, dtype=float))
        return HitRecord(h.kind, h.target, h.raw, h.normalized, conf, _round_poly(poly), True,
                         ln.text, engine, variant.name, seq)

    # ---------------------------------------------------------------- state
    def _confident(self, h: HitRecord) -> bool:
        return h.kind in MATCH_KINDS and h.confidence is not None and h.confidence >= self.opts.match_confidence

    def _conflicted(self, h: HitRecord) -> bool:
        return any(o.kind == EXCLUDED and o.engine != h.engine and _overlap(o.poly, h.poly) > 0.4
                   for o in self.hits)

    def has_confident_match(self) -> bool:
        return any(self._confident(h) and not self._conflicted(h) for h in self.hits)

    def done(self) -> bool:
        return (not self.opts.thorough) and self.has_confident_match()


def analyze(data: bytes, opts: Options | None = None, primary: OcrEngine | None = None,
            secondary: OcrEngine | None = None, secondary_error: str | None = None,
            max_pixels: int | None = None, should_stop: Callable[[], bool] | None = None) -> Analysis:
    """Analyse one image.  Raises imaging.ImageLoadError for undecodable files
    and InterruptedError when ``should_stop`` fires."""
    opts = opts or Options()
    if primary is None:
        from .ocr.registry import primary_engine, secondary_engine
        primary = primary_engine()
        try:
            secondary = secondary_engine()
            if secondary is None:
                secondary_error = "second OCR engine disabled in configuration"
        except EngineUnavailable as exc:
            secondary, secondary_error = None, str(exc)
    loaded = imaging.load_image(data, max_pixels or settings.max_pixels)
    return analyze_loaded(loaded, opts, primary, secondary, secondary_error, should_stop)


def analyze_loaded(loaded: imaging.LoadedImage, opts: Options, primary: OcrEngine,
                   secondary: OcrEngine | None, secondary_error: str | None,
                   should_stop: Callable[[], bool] | None = None) -> Analysis:
    rgb = loaded.rgb
    h, w = rgb.shape[:2]
    warnings = list(loaded.warnings)
    sharp = imaging.sharpness(rgb)
    r = _Run(loaded, opts, primary, secondary, secondary_error, should_stop)

    # ---- Stage A: original image, native resolution ----------------------
    orig = imaging.Variant("original", rgb, lambda p: np.asarray(p, dtype=float), "original image, native resolution")
    r.original_lines = r.run("A", primary, orig) or []

    # ---- Stage B: preprocessing variants ----------------------------------
    if not r.done():
        stage_b: list[Callable[[], imaging.Variant | list[imaging.Variant]]] = [
            lambda: imaging.rotate(rgb, "cw90"),
            lambda: imaging.rotate(rgb, "ccw90"),
        ]
        if max(h, w) <= 1800:
            factor = 3.0 if max(h, w) <= 700 else 2.0
            stage_b.append(lambda: imaging.upscale(rgb, factor))
        else:
            stage_b.append(lambda: imaging.tiles(rgb))
        stage_b += [
            lambda: imaging.gray_clahe(rgb),
            lambda: imaging.saturation_channel(rgb),
            lambda: imaging.chroma_channel(rgb),
            lambda: imaging.median_denoise(rgb),
            lambda: imaging.adaptive_binarize(rgb, 2.0 if max(h, w) <= 1500 else 1.0),
            lambda: imaging.inverted(rgb),
            lambda: imaging.sharpen_denoise(rgb),
            lambda: imaging.brightness_contrast(rgb),
            lambda: imaging.rotate(rgb, "r180"),
        ]
        if loaded.alpha_on_black is not None:
            stage_b.insert(0, lambda: imaging.Variant(
                "alpha_on_black", loaded.alpha_on_black, lambda p: np.asarray(p, dtype=float),
                "transparent areas composited on black"))
        for make in stage_b:
            if r.done():
                break
            v = make()
            if v is None:
                continue
            for vv in (v if isinstance(v, list) else [v]):
                r.run("B", primary, vv)
                if r.done():
                    break

    # ---- Stage R: region re-OCR of uncertain areas -------------------------
    unresolved_regions = 0
    if not r.done():
        targets: list[tuple[list, str]] = []
        for hh in r.hits:
            if hh.kind in CANDIDATE_KINDS or (hh.kind in MATCH_KINDS and not r._confident(hh)) \
                    or (hh.kind in MATCH_KINDS and r._conflicted(hh)):
                targets.append((hh.poly, "candidate"))
        low_lines = [ln for ln in r.original_lines
                     if (ln["conf"] is None or ln["conf"] < opts.low_confidence)
                     and sum(ch.isalpha() for ch in ln["text"]) >= 4]
        for ln in low_lines:
            targets.append((ln["poly"], "lowconf"))
        # de-duplicate overlapping regions
        uniq: list[tuple[list, str]] = []
        for poly, why in targets:
            if not any(_overlap(poly, u[0]) > 0.7 for u in uniq):
                uniq.append((poly, why))
        for poly, why in uniq[: opts.max_regions]:
            if r.done():
                break
            v = imaging.crop_region(rgb, poly)
            if v is None:
                continue
            got = r.run("R", primary, v) or []
            sec_got: list[dict] = []
            if r.secondary is not None and not r.done():
                sec_got = r.run("R", r.secondary, v, "block") or []
            if why == "lowconf":
                confs = [l["conf"] for l in got + sec_got if l["conf"] is not None]
                if not confs or max(confs) < opts.low_confidence:
                    unresolved_regions += 1
        if len(uniq) > opts.max_regions:
            warnings.append(f"{len(uniq) - opts.max_regions} uncertain regions were not re-read (limit {opts.max_regions})")
            unresolved_regions += len(uniq) - opts.max_regions

    # ---- Stage C: independent second engine -------------------------------
    if not r.done() and r.secondary is not None:
        sec = r.secondary
        small = max(h, w) <= 1500
        stage_c = [
            (orig, "auto"),
            (imaging.otsu_binarize(rgb, 2.0 if small else 1.0), "sparse"),
            (imaging.rotate(rgb, "cw90"), "sparse"),
            (imaging.rotate(rgb, "ccw90"), "sparse"),
        ]
        for v, mode in stage_c:
            if r.done():
                break
            r.run("C", sec, v, mode)

    return _decide(r, loaded, warnings, sharp, unresolved_regions)


def _decide(r: _Run, loaded: imaging.LoadedImage, warnings: list[str], sharp: float,
            unresolved_regions: int) -> Analysis:
    opts = r.opts
    # Cluster match-kind hits from all passes into physical occurrences.
    match_hits = [h for h in r.hits if h.kind in MATCH_KINDS]
    clusters: list[list[HitRecord]] = []
    for hh in sorted(match_hits, key=lambda x: -(x.confidence or 0)):
        for c in clusters:
            if _overlap(c[0].poly, hh.poly) > 0.4:
                c.append(hh)
                break
        else:
            clusters.append([hh])
    for i, c in enumerate(clusters, 1):
        for hh in c:
            hh.occurrence = i
    confident_clusters = [c for c in clusters
                          if any(r._confident(x) for x in c) and not any(r._conflicted(x) for x in c)]
    conflicted_clusters = [c for c in clusters if any(r._conflicted(x) for x in c)]
    weak_clusters = [c for c in clusters if c not in confident_clusters and c not in conflicted_clusters]

    reasons: list[str] = []
    failed = [p for p in r.passes if p.status == "error"]
    if failed:
        warnings.append(f"{len(failed)} OCR pass(es) failed: " +
                        "; ".join(f"{p.engine}/{p.variant}: {p.error}" for p in failed[:3]))

    best = max((x.confidence for x in match_hits if x.confidence is not None), default=None)
    if confident_clusters:
        status = MATCH
        for c in confident_clusters[:5]:
            top = max(c, key=lambda x: x.confidence or 0)
            reasons.append(f"'{top.raw}' read by {top.engine} on {top.variant} "
                           f"(confidence {top.confidence:.2f})")
        if conflicted_clusters or weak_clusters:
            warnings.append(f"{len(conflicted_clusters) + len(weak_clusters)} further possible occurrence(s) "
                            "were uncertain and are not counted")
        occ = confident_clusters
    else:
        status = REVIEW
        occ = []
        for c in weak_clusters[:5]:
            top = max(c, key=lambda x: x.confidence or 0)
            cf = "n/a" if top.confidence is None else f"{top.confidence:.2f}"
            reasons.append(f"possible match '{top.raw}' below confidence threshold ({cf}, {top.engine}/{top.variant})")
        for c in conflicted_clusters[:5]:
            top = c[0]
            other = next(o for o in r.hits if o.kind == EXCLUDED and _overlap(o.poly, top.poly) > 0.4)
            reasons.append(f"engines disagree at the same location: '{top.raw}' ({top.engine}) vs "
                           f"'{other.raw}' ({other.engine})")
        cands = [x for x in r.hits if x.kind in CANDIDATE_KINDS]
        seen: set[str] = set()
        for x in cands:
            key = f"{x.kind}:{x.raw.lower()}"
            if key in seen:
                continue
            seen.add(key)
            if len(seen) > 6:
                break
            label = {"confusion": "OCR-confusion candidate", "near": "near-miss reading",
                     "compound": "merged/compound word"}[x.kind]
            reasons.append(f"{label} '{x.raw}' ({x.engine}/{x.variant})")
        if failed:
            reasons.append(f"{len(failed)} OCR pass(es) failed")
        if r.secondary is None:
            reasons.append(f"independent second-engine verification not performed: {r.secondary_error}")
        if sharp < opts.blur_threshold:
            reasons.append(f"image has almost no visual detail - blank or heavily blurred (sharpness {sharp:.1f}); "
                           "confirm visually")
        if min(loaded.width, loaded.height) < 32:
            reasons.append(f"image is too small to read reliably ({loaded.width}x{loaded.height})")
        if any("multi-frame" in wn for wn in loaded.warnings):
            reasons.append("multi-page/multi-frame file: only the first frame was analysed")
        if unresolved_regions:
            reasons.append(f"{unresolved_regions} text region(s) could not be read with confidence by either engine")
        if not reasons:
            status = NO_MATCH
            engines = sorted({p.engine for p in r.passes if p.status == "ok"})
            reasons.append(f"no target word in {len(r.passes)} OCR passes "
                           f"({', '.join(engines)}); no candidates or unresolved regions")

    counts: dict[str, int] = {"cocktail": 0, "cocktails": 0}
    for c in occ:
        counts[max(c, key=lambda x: x.confidence or 0).target] += 1
    words = sorted(k for k, v in counts.items() if v)
    return Analysis(status, reasons, warnings, loaded.width, loaded.height, loaded.format,
                    best, len(occ), words, counts, r.passes, r.hits, round(sharp, 2))
