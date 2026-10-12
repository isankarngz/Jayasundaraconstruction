"""Pluggable OCR engines.  Add a new engine by subclassing OcrEngine and
registering it in registry.py."""
from .base import OcrEngine, OcrLine, OcrWord, EngineUnavailable  # noqa: F401
