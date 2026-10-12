"""Runtime configuration, read from environment variables (prefix CF_)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(f"CF_{name}", default)


def _bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "./data")).resolve())
    # Number of images processed concurrently.  Each worker runs its own OCR
    # engine instances; onnxruntime already uses several threads per worker.
    workers: int = field(default_factory=lambda: int(_env("WORKERS", "1")))
    ocr_threads: int = field(default_factory=lambda: int(_env("OCR_THREADS", "0")))  # 0 = auto
    max_file_bytes: int = field(default_factory=lambda: int(_env("MAX_FILE_MB", "200")) * 1024 * 1024)
    max_pixels: int = field(default_factory=lambda: int(_env("MAX_MEGAPIXELS", "150")) * 1_000_000)
    max_attempts: int = field(default_factory=lambda: int(_env("MAX_ATTEMPTS", "2")))
    enable_tesseract: bool = field(default_factory=lambda: _bool("ENABLE_TESSERACT", True))
    tesseract_cmd: str = field(default_factory=lambda: _env("TESSERACT_CMD", "tesseract"))
    tesseract_timeout: int = field(default_factory=lambda: int(_env("TESSERACT_TIMEOUT", "120")))
    autostart_workers: bool = field(default_factory=lambda: _bool("AUTOSTART_WORKERS", True))
    frontend_dir: Path = field(default_factory=lambda: Path(
        _env("FRONTEND_DIR", str(Path(__file__).resolve().parents[2] / "frontend" / "dist"))))

    # --- decision thresholds (see pipeline.py for how they are used) -------
    # An EXACT hit at or above this recognition confidence is a confirmed match.
    match_confidence: float = field(default_factory=lambda: float(_env("MATCH_CONFIDENCE", "0.60")))
    # Any recognised line below this confidence counts as "uncertain text".
    low_confidence: float = field(default_factory=lambda: float(_env("LOW_CONFIDENCE", "0.50")))
    # Images whose Laplacian-variance sharpness is below this are flagged.
    blur_threshold: float = field(default_factory=lambda: float(_env("BLUR_THRESHOLD", "12")))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cocktail_finder.sqlite3"

    @property
    def originals_dir(self) -> Path:
        return self.data_dir / "originals"

    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.originals_dir, self.derived_dir, self.exports_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
