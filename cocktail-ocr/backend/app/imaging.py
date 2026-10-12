"""Image validation, decoding and preprocessing variants.

Every variant carries an inverse transform that maps points in the variant's
pixel space back to the *original* (EXIF-orientation-corrected) image, so all
bounding boxes shown to the reviewer line up with the original image.
"""
from __future__ import annotations

import io
import warnings
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "TIFF", "MPO"}
WEB_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "MPO"}

# Magic numbers for a cheap first-pass sniff before Pillow touches the bytes.
_MAGIC = (
    (b"\xff\xd8\xff", "JPEG"),
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"BM", "BMP"),
    (b"II*\x00", "TIFF"),
    (b"MM\x00*", "TIFF"),
    (b"II+\x00", "TIFF"),  # BigTIFF
    (b"MM\x00+", "TIFF"),
)

Points = np.ndarray  # shape (N, 2) float


def sniff_format(head: bytes) -> str | None:
    for magic, fmt in _MAGIC:
        if head.startswith(magic):
            return fmt
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    return None


class ImageLoadError(Exception):
    """The file could not be decoded as an image (corrupt, truncated, bomb...)."""


@dataclass
class LoadedImage:
    rgb: np.ndarray                  # HxWx3 uint8, EXIF orientation applied
    format: str
    width: int
    height: int
    has_alpha: bool
    alpha_on_black: np.ndarray | None = None
    warnings: list[str] = field(default_factory=list)
    exif_rotated: bool = False


def load_image(data: bytes, max_pixels: int) -> LoadedImage:
    Image.MAX_IMAGE_PIXELS = max_pixels
    warn: list[str] = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            im = Image.open(io.BytesIO(data))
            fmt = im.format or "?"
            n_frames = getattr(im, "n_frames", 1)
            if n_frames > 1:
                warn.append(f"multi-frame file ({n_frames} frames/pages): only the first frame was analysed")
            im.seek(0)
            im.load()
            orientation = im.getexif().get(0x0112, 1) if hasattr(im, "getexif") else 1
            im = ImageOps.exif_transpose(im)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageLoadError(f"rejected as possible decompression bomb: {exc}") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise ImageLoadError(f"cannot decode image: {exc}") from exc

    has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
    alpha_black = None
    if im.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        arr = np.asarray(im, dtype=np.float64)
        lo, hi = float(arr.min()), float(arr.max())
        arr = ((arr - lo) / (hi - lo or 1) * 255).astype(np.uint8)
        im = Image.fromarray(arr, "L")
        warn.append("high bit-depth image was rescaled to 8 bits for OCR")
    if has_alpha:
        rgba = im.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        black = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        rgb = Image.alpha_composite(white, rgba).convert("RGB")
        alpha_black = np.asarray(Image.alpha_composite(black, rgba).convert("RGB"))
    else:
        rgb = im.convert("RGB")
    arr = np.ascontiguousarray(np.asarray(rgb))
    h, w = arr.shape[:2]
    return LoadedImage(arr, fmt, w, h, has_alpha, alpha_black, warn,
                       exif_rotated=orientation not in (1, None))


# ---------------------------------------------------------------- variants --

@dataclass
class Variant:
    name: str
    image: np.ndarray                        # RGB or single-channel uint8
    to_orig: Callable[[Points], Points]
    description: str = ""


def _identity(p: Points) -> Points:
    return np.asarray(p, dtype=float)


def _scale_back(f: float, ox: float = 0.0, oy: float = 0.0) -> Callable[[Points], Points]:
    def inv(p: Points) -> Points:
        p = np.asarray(p, dtype=float)
        return np.stack([p[:, 0] / f + ox, p[:, 1] / f + oy], axis=1)
    return inv


def rotate(img: np.ndarray, which: str) -> Variant:
    """which: cw90 | ccw90 | r180"""
    h, w = img.shape[:2]
    if which == "cw90":
        out = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)

        def inv(p):  # rotated (u,v) -> original (x,y): x=v, y=h-1-u
            p = np.asarray(p, dtype=float)
            return np.stack([p[:, 1], (h - 1) - p[:, 0]], axis=1)
    elif which == "ccw90":
        out = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)

        def inv(p):  # (u,v) -> x=w-1-v, y=u
            p = np.asarray(p, dtype=float)
            return np.stack([(w - 1) - p[:, 1], p[:, 0]], axis=1)
    else:
        out = cv2.rotate(img, cv2.ROTATE_180)

        def inv(p):
            p = np.asarray(p, dtype=float)
            return np.stack([(w - 1) - p[:, 0], (h - 1) - p[:, 1]], axis=1)
    return Variant(f"rotate_{which}", out, inv, f"rotated {which}")


def to_gray(img: np.ndarray) -> np.ndarray:
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)


def upscale(img: np.ndarray, factor: float) -> Variant:
    out = cv2.resize(img, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
    return Variant(f"upscale_x{factor:g}", out, _scale_back(factor), f"upscaled x{factor:g} (cubic)")


def gray_clahe(img: np.ndarray) -> Variant:
    g = to_gray(img)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    out = clahe.apply(g)
    # Global contrast stretch after local equalisation.
    out = cv2.normalize(out, None, 0, 255, cv2.NORM_MINMAX)
    return Variant("gray_clahe", out, _identity, "grayscale + CLAHE contrast enhancement")


def adaptive_binarize(img: np.ndarray, factor: float = 1.0) -> Variant:
    g = to_gray(img)
    if factor != 1.0:
        g = cv2.resize(g, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    block = max(15, (int(min(g.shape[:2]) / 30) | 1))
    out = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, 10)
    name = "adaptive_binarize" + (f"_x{factor:g}" if factor != 1.0 else "")
    return Variant(name, out, _scale_back(factor), f"adaptive Gaussian threshold (block {block})")


def otsu_binarize(img: np.ndarray, factor: float = 1.0) -> Variant:
    g = to_gray(img)
    if factor != 1.0:
        g = cv2.resize(g, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
    _, out = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    name = "otsu_binarize" + (f"_x{factor:g}" if factor != 1.0 else "")
    return Variant(name, out, _scale_back(factor), "Otsu global threshold")


def inverted(img: np.ndarray) -> Variant:
    return Variant("inverted", 255 - img, _identity, "colour inversion (light text on dark)")


def sharpen_denoise(img: np.ndarray) -> Variant:
    src = img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    h, w = src.shape[:2]
    if h * w <= 12_000_000:
        den = cv2.fastNlMeansDenoisingColored(src, None, 7, 7, 7, 21)
    else:
        den = cv2.bilateralFilter(src, 7, 50, 50)
    blur = cv2.GaussianBlur(den, (0, 0), 2.0)
    out = cv2.addWeighted(den, 1.8, blur, -0.8, 0)
    return Variant("sharpen_denoise", out, _identity, "non-local-means denoise + unsharp mask")


def brightness_contrast(img: np.ndarray) -> Variant:
    g = to_gray(img).astype(np.float32)
    lo, hi = np.percentile(g, 1), np.percentile(g, 99)
    out = np.clip((g - lo) * (255.0 / max(1.0, hi - lo)), 0, 255).astype(np.uint8)
    # gamma lift for dark images
    if out.mean() < 90:
        lut = (np.linspace(0, 1, 256) ** 0.6 * 255).astype(np.uint8)
        out = cv2.LUT(out, lut)
    return Variant("contrast_stretch", out, _identity, "percentile contrast stretch / gamma")


def tiles(img: np.ndarray, tile: int = 900, overlap: int = 180, factor: float = 2.0) -> list[Variant]:
    """Overlapping crops, each upscaled, so small text in large images is
    presented to the detector at a usable size."""
    h, w = img.shape[:2]
    out: list[Variant] = []
    step = tile - overlap
    ys = list(range(0, max(1, h - overlap), step)) or [0]
    xs = list(range(0, max(1, w - overlap), step)) or [0]
    for y in ys:
        for x in xs:
            crop = img[y:y + tile, x:x + tile]
            if crop.shape[0] < 16 or crop.shape[1] < 16:
                continue
            up = cv2.resize(crop, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
            out.append(Variant(f"tile_{x}_{y}_x{factor:g}", up, _scale_back(factor, x, y),
                               f"tile at ({x},{y}) {tile}px, upscaled x{factor:g}"))
    return out


def crop_region(img: np.ndarray, poly: Points, pad: float = 0.25, min_h: int = 64,
                max_factor: float = 4.0) -> Variant | None:
    """Axis-aligned crop around a text polygon (original coords), upscaled."""
    h, w = img.shape[:2]
    p = np.asarray(poly, dtype=float)
    x0, y0 = p.min(axis=0)
    x1, y1 = p.max(axis=0)
    bw, bh = x1 - x0, y1 - y0
    px, py = bw * pad + 4, bh * pad + 4
    x0, y0 = int(max(0, x0 - px)), int(max(0, y0 - py))
    x1, y1 = int(min(w, x1 + px)), int(min(h, y1 + py))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    crop = img[y0:y1, x0:x1]
    short = min(crop.shape[:2])
    factor = float(min(max_factor, max(1.0, min_h / max(1, short))))
    if factor > 1.0:
        crop = cv2.resize(crop, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
    return Variant(f"region_{x0}_{y0}", crop, _scale_back(factor, x0, y0),
                   f"crop ({x0},{y0})-({x1},{y1}) x{factor:.2g}")


# ------------------------------------------------------------ diagnostics --

def sharpness(img: np.ndarray) -> float:
    g = to_gray(img)
    hh, ww = g.shape[:2]
    scale = 1000.0 / max(hh, ww)
    if scale < 1:
        g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def make_thumbnail(rgb: np.ndarray, max_side: int = 360) -> bytes:
    im = Image.fromarray(rgb)
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return buf.getvalue()


def make_view(rgb: np.ndarray) -> bytes:
    """Browser-displayable full-resolution copy (for TIFF / EXIF-rotated files)."""
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "JPEG", quality=95)
    return buf.getvalue()


def saturation_channel(img: np.ndarray) -> Variant | None:
    """HSV saturation, contrast-normalised.  Recovers coloured text whose
    brightness matches the background (common on posters and busy textures)."""
    if img.ndim != 3:
        return None
    s = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)[:, :, 1]
    out = cv2.normalize(s, None, 0, 255, cv2.NORM_MINMAX)
    return Variant("hsv_saturation", out, _identity, "HSV saturation channel (colour contrast)")


def chroma_channel(img: np.ndarray) -> Variant | None:
    """LAB chroma (distance from grey) - a second colour-contrast view."""
    if img.ndim != 3:
        return None
    lab = cv2.cvtColor(img, cv2.COLOR_RGB2LAB).astype(np.float32)
    chroma = np.sqrt((lab[:, :, 1] - 128) ** 2 + (lab[:, :, 2] - 128) ** 2)
    out = cv2.normalize(chroma, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    return Variant("lab_chroma", out, _identity, "LAB chroma channel (colour contrast)")


def median_denoise(img: np.ndarray) -> Variant:
    out = cv2.medianBlur(img, 5)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    out = clahe.apply(to_gray(out))
    return Variant("median_clahe", out, _identity, "median filter (impulse noise) + CLAHE")
