"""Pipeline tests.  Most use the real OCR engines on rendered images; the
decision-policy tests use scripted fake engines so each escalation rule is
exercised deterministically."""
import io

import numpy as np
import pytest
from PIL import Image

from app import imaging, pipeline
from app.ocr.base import OcrEngine, OcrLine
from app.ocr.registry import primary_engine, secondary_engine


def run(path, **kw):
    return pipeline.analyze(path.read_bytes(), pipeline.Options(**kw))


# ------------------------------------------------------------ real engines --

POSITIVE_CASES = [
    "p01_title_case.png", "p02_uppercase.jpg", "p03_lowercase_italic.png", "p05_parenthesis.webp",
    "p07_small_text_large_image.jpg", "p08_rotated_90.png", "p09_rotated_270.png", "p10_upside_down.png",
    "p12_low_contrast.png", "p13_blurred.jpg", "p14_pattern_background.png", "p18_letter_spaced.png",
    "p20_tiff.tif", "h01_10px_text_3000px.png", "h02_heavy_blur.png", "h05_camouflaged.png",
    "h06_small_sideways.png",
]


@pytest.mark.parametrize("name", POSITIVE_CASES)
def test_positive_images_match(dataset, name):
    d, _ = dataset
    a = run(d / name)
    assert a.status == pipeline.MATCH, (name, a.reasons)
    assert a.occurrences >= 1
    assert a.hits and all(len(h.poly) == 4 for h in a.hits)


def test_hit_box_lies_on_the_word_for_rotated_text(dataset):
    d, _ = dataset
    a = run(d / "p08_rotated_90.png")
    occ = [h for h in a.hits if h.occurrence == 1]
    xs = [p[0] for h in occ for p in h.poly]
    ys = [p[1] for h in occ for p in h.poly]
    # text rotated 90 degrees -> box is tall and narrow, inside the 900x900 canvas
    assert (max(ys) - min(ys)) > 2 * (max(xs) - min(xs))
    assert 0 <= min(xs) and max(xs) <= 900 and 0 <= min(ys) and max(ys) <= 900


def test_multiple_occurrences_counted(dataset):
    d, _ = dataset
    a = run(d / "p15_multiple.png", thorough=True)
    assert a.status == pipeline.MATCH
    assert a.occurrences >= 3
    assert a.word_counts["cocktail"] >= 1 and a.word_counts["cocktails"] >= 2


def test_mocktail_only_is_not_a_match(dataset):
    d, _ = dataset
    a = run(d / "n01_mocktail.png")
    assert a.status != pipeline.MATCH
    assert any(h.kind == "excluded" for h in a.hits)


@pytest.mark.parametrize("name", ["n02_other_menu.jpg", "n04_partial_words.png", "n06_rotated_other.png"])
def test_negative_images_are_never_auto_matched(dataset, name):
    d, _ = dataset
    a = run(d / name)
    assert a.status in (pipeline.NO_MATCH, pipeline.REVIEW), a.reasons
    if a.status == pipeline.NO_MATCH:
        engines = {p.engine for p in a.passes if p.status == "ok"}
        assert len(engines) == 2, "a verified negative must have used both engines"
        assert len(a.passes) >= 10


def test_blank_image_goes_to_review(dataset):
    d, _ = dataset
    a = run(d / "n03_blank.png")
    assert a.status == pipeline.REVIEW


def test_corrupted_file_raises_load_error(dataset):
    d, _ = dataset
    data = (d / "p02_uppercase.jpg").read_bytes()[:2000]
    with pytest.raises(imaging.ImageLoadError):
        pipeline.analyze(data)
    with pytest.raises(imaging.ImageLoadError):
        pipeline.analyze(b"this is not an image")


def test_exif_orientation_is_applied():
    im = Image.new("RGB", (40, 20), "white")
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 CW on display
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    loaded = imaging.load_image(buf.getvalue(), 10_000_000)
    assert (loaded.width, loaded.height) == (20, 40)
    assert loaded.exif_rotated


def test_multiframe_tiff_flagged():
    frames = [Image.new("RGB", (300, 200), c) for c in ("white", "black")]
    buf = io.BytesIO()
    frames[0].save(buf, "TIFF", save_all=True, append_images=frames[1:])
    loaded = imaging.load_image(buf.getvalue(), 10_000_000)
    assert any("multi-frame" in w for w in loaded.warnings)


def test_decompression_bomb_rejected():
    buf = io.BytesIO()
    Image.new("L", (5000, 5000)).save(buf, "PNG")
    with pytest.raises(imaging.ImageLoadError):
        imaging.load_image(buf.getvalue(), max_pixels=1_000_000)


# ------------------------------------------------------- scripted engines --

class Fake(OcrEngine):
    """Returns a scripted reading for the original image and nothing else."""

    def __init__(self, name, original=None, everywhere=None, fail=False):
        self.name = name
        self.original = original or []
        self.everywhere = everywhere or []
        self.fail = fail
        self.calls = 0

    def recognize(self, image, mode="auto"):
        self.calls += 1
        if self.fail:
            raise RuntimeError("engine crashed")
        lines = list(self.everywhere)
        if self.calls == 1:
            lines += self.original
        return [OcrLine(t, c, [[10, 10], [200, 10], [200, 40], [10, 40]]) for t, c in lines]


def textured(seed=0):
    rng = np.random.default_rng(seed)
    return imaging.LoadedImage(rng.integers(0, 255, (300, 400, 3), dtype=np.uint8), "PNG", 400, 300, False)


def decide(primary, secondary, err=None, **kw):
    return pipeline.analyze_loaded(textured(), pipeline.Options(**kw), primary, secondary, err)


def test_confident_exact_match_stops_early():
    p = Fake("p", original=[("Cocktail Menu", 0.95)])
    a = decide(p, Fake("s"))
    assert a.status == pipeline.MATCH and len(a.passes) == 1


def test_low_confidence_exact_hit_is_review():
    a = decide(Fake("p", everywhere=[("Cocktail Menu", 0.3)]), Fake("s"))
    assert a.status == pipeline.REVIEW
    assert any("below confidence" in r for r in a.reasons)


def test_confusion_candidate_is_review():
    a = decide(Fake("p", everywhere=[("cocktaiI bar", 0.9)]), Fake("s"))
    assert a.status == pipeline.REVIEW
    assert any("confusion" in r for r in a.reasons)


def test_engine_disagreement_cocktail_vs_mocktail_is_review():
    a = decide(Fake("p", everywhere=[("Mocktail list", 0.9)]), Fake("s", everywhere=[("Cocktail list", 0.9)]))
    assert a.status == pipeline.REVIEW
    assert any("disagree" in r for r in a.reasons)


def test_secondary_finds_what_primary_missed():
    a = decide(Fake("p", everywhere=[("Wine list", 0.9)]), Fake("s", everywhere=[("COCKTAILS", 0.92)]))
    assert a.status == pipeline.MATCH


def test_failed_pass_prevents_negative():
    a = decide(Fake("p", everywhere=[("Wine list", 0.9)]), Fake("s", fail=True))
    assert a.status == pipeline.REVIEW
    assert any("failed" in r for r in a.reasons)


def test_missing_second_engine_prevents_negative():
    a = decide(Fake("p", everywhere=[("Wine list", 0.9)]), None, "tesseract not installed")
    assert a.status == pipeline.REVIEW
    assert any("second-engine" in r for r in a.reasons)


def test_unreadable_low_confidence_text_is_review():
    a = decide(Fake("p", everywhere=[("Xqzvbn wlrtp", 0.2)]), Fake("s", everywhere=[("Xqzvbn", 0.2)]))
    assert a.status == pipeline.REVIEW
    assert any("could not be read" in r for r in a.reasons)


def test_clean_negative_is_no_match_with_both_engines():
    p, s = Fake("p", everywhere=[("Wine list", 0.95)]), Fake("s", everywhere=[("Wine list", 0.9)])
    a = decide(p, s)
    assert a.status == pipeline.NO_MATCH
    assert p.calls >= 10 and s.calls >= 4


def test_real_engines_available():
    assert primary_engine().name
    assert secondary_engine() is not None
