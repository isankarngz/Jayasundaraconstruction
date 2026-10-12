"""Generate a labelled synthetic evaluation dataset.

Each image's ground-truth label is fixed by construction (we render the exact
text ourselves), so labels are known without OCR.  This set exercises the
hard cases listed in the requirements; it is NOT a substitute for a labelled
sample of your own real photographs - see README "Measuring accuracy on your
own images".

Usage:  python scripts/make_dataset.py OUT_DIR
Writes OUT_DIR/*.png|jpg|webp|tif and OUT_DIR/labels.csv (filename,label,case)
"""
from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_DIRS = [Path("/usr/share/fonts"), Path("/Library/Fonts"), Path("C:/Windows/Fonts")]
FONT_PREFS = ["DejaVuSans.ttf", "DejaVuSerif-Bold.ttf", "LiberationSerif-Italic.ttf",
              "FreeSansBoldOblique.ttf", "LiberationSans-Regular.ttf", "DejaVuSansMono.ttf",
              "Carlito-Bold.ttf", "arial.ttf", "Arial.ttf"]


def _find_fonts() -> list[Path]:
    found: list[Path] = []
    for name in FONT_PREFS:
        for d in FONT_DIRS:
            if d.exists():
                hit = next(d.rglob(name), None)
                if hit:
                    found.append(hit)
                    break
    return found


FONTS = _find_fonts()


def font(i: int, size: int):
    if not FONTS:
        return ImageFont.load_default(size=size)
    return ImageFont.truetype(str(FONTS[i % len(FONTS)]), size)


def canvas(w=1200, h=800, bg=(250, 247, 240)):
    im = Image.new("RGB", (w, h), bg)
    return im, ImageDraw.Draw(im)


def text_block(lines, w=1200, h=800, size=48, fi=0, fg=(20, 20, 20), bg=(250, 247, 240), x=60, y=60, gap=1.5):
    im, d = canvas(w, h, bg)
    f = font(fi, size)
    for i, ln in enumerate(lines):
        d.text((x, y + i * int(size * gap)), ln, fill=fg, font=f)
    return im


def rotated_text(txt, angle, size=56, fi=0, w=900, h=900):
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text((80, h // 2 - size), txt, fill=(10, 10, 10, 255), font=font(fi, size))
    d.text((80, h // 2 + size), "Happy hour 5-7pm", fill=(10, 10, 10, 255), font=font(fi, int(size * 0.6)))
    layer = layer.rotate(angle, expand=False, resample=Image.BICUBIC)
    bg = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    return Image.alpha_composite(bg, layer).convert("RGB")


def pattern_bg(w=1200, h=800, seed=0):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = (np.sin(xx / 9.0) * np.cos(yy / 13.0) * 60 + 150).astype(np.int16)
    noise = rng.integers(-35, 35, (h, w))
    arr = np.clip(base + noise, 0, 255).astype(np.uint8)
    rgb = np.stack([arr, (arr * 0.8).astype(np.uint8), (arr * 0.6).astype(np.uint8)], -1)
    return Image.fromarray(rgb)


FILLER = ["Wine list", "Draught beer", "Fresh juices", "Espresso", "House red", "Sparkling water",
          "Grilled salmon", "Caesar salad", "Opening hours", "Live music Friday", "Desserts"]


def build(out: Path) -> list[tuple[str, str, str]]:
    out.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, str, str]] = []
    rnd = random.Random(7)

    def save(im: Image.Image, name: str, label: str, case: str, **kw):
        im.save(out / name, **kw)
        rows.append((name, label, case))

    # ---- positives ---------------------------------------------------------
    save(text_block(["Cocktail Menu", "Mojito 9.50", "Negroni 10.00"]), "p01_title_case.png", "positive", "title case")
    save(text_block(["SIGNATURE COCKTAILS", "OLD FASHIONED", "MARGARITA"], fi=1), "p02_uppercase.jpg", "positive", "uppercase serif", quality=90)
    save(text_block(["today: cocktail specials", "two for one"], fi=2, size=44), "p03_lowercase_italic.png", "positive", "lowercase italic")
    save(text_block(["Non-alcoholic cocktails available", "ask your server"], fi=3), "p04_hyphenated_context.png", "positive", "non-alcoholic cocktails")
    save(text_block(["Cocktail(s) of the day"], fi=4), "p05_parenthesis.webp", "positive", "Cocktail(s)", quality=92)
    save(text_block(["\"Cocktails!\" she said.", "Mocktails also served"], fi=0), "p06_punctuation_and_mocktail.png", "positive", "punctuation + mocktail lookalike")

    # very small text in a large image
    im = text_block([rnd.choice(FILLER) for _ in range(6)], w=2400, h=1800, size=40)
    d = ImageDraw.Draw(im)
    for i in range(12):
        d.text((120, 700 + i * 22), "fine print " + rnd.choice(FILLER), fill=(60, 60, 60), font=font(4, 15))
    d.text((1500, 1500), "Cocktails served until midnight", fill=(40, 40, 40), font=font(4, 15))
    save(im, "p07_small_text_large_image.jpg", "positive", "15px text in 2400x1800", quality=92)

    save(rotated_text("COCKTAILS", 90), "p08_rotated_90.png", "positive", "sideways (90)")
    save(rotated_text("Cocktail bar", -90, fi=1), "p09_rotated_270.png", "positive", "sideways (270)")
    save(rotated_text("Cocktail list", 180, fi=4), "p10_upside_down.png", "positive", "upside down")
    save(rotated_text("Cocktails", 17, fi=0), "p11_skewed.png", "positive", "17 degree skew")

    save(text_block(["Cocktail Hour", "Wine list"], fg=(128, 128, 128), bg=(150, 150, 150), size=52), "p12_low_contrast.png", "positive", "low contrast")
    save(text_block(["COCKTAILS", "HAPPY HOUR"], size=64, fi=1).filter(ImageFilter.GaussianBlur(2.2)), "p13_blurred.jpg", "positive", "gaussian blur r=2.2", quality=88)

    im = pattern_bg(seed=1)
    ImageDraw.Draw(im).text((120, 300), "Signature Cocktails", fill=(255, 255, 255), font=font(1, 72),
                            stroke_width=2, stroke_fill=(0, 0, 0))
    save(im, "p14_pattern_background.png", "positive", "text over patterned background")

    save(text_block(["Cocktails", "Classic cocktails", "Frozen COCKTAILS", "cocktail of the week"], fi=3), "p15_multiple.png", "positive", "4 occurrences")
    save(text_block(["COCKTAILS", "after dark"], fg=(240, 240, 240), bg=(18, 22, 30), size=64), "p16_light_on_dark.png", "positive", "light text on dark")
    save(text_block(["Cócteles / Cocktails / カクテル"], fi=0, size=46), "p17_multilingual.png", "positive", "multilingual line")
    save(text_block(["C O C K T A I L S"], fi=1, size=50), "p18_letter_spaced.png", "positive", "letter-spaced heading")
    small = text_block(["Cocktails"], w=260, h=70, size=34, x=10, y=12)
    save(small, "p19_tiny_image.png", "positive", "260x70 image")
    save(text_block(["Cocktail Menu", "Spritz"], fi=4), "p20_tiff.tif", "positive", "TIFF format")

    # ---- hard positives ----------------------------------------------------
    im = text_block([rnd.choice(FILLER) for _ in range(4)], w=3000, h=2000, size=36)
    ImageDraw.Draw(im).text((2100, 1700), "cocktails", fill=(70, 70, 70), font=font(0, 10))
    save(im, "h01_10px_text_3000px.png", "positive", "10px text in 3000x2000")
    save(text_block(["COCKTAILS", "HAPPY HOUR"], size=56, fi=0).filter(ImageFilter.GaussianBlur(3.6)), "h02_heavy_blur.png", "positive", "gaussian blur r=3.6")
    save(text_block(["Cocktail Hour", "Wine list"], fg=(140, 140, 140), bg=(148, 148, 148), size=52), "h03_very_low_contrast.png", "positive", "8-level contrast")
    save(text_block(["Signature Cocktails", "Old fashioned"], size=30, fi=4), "h04_jpeg_q6.jpg", "positive", "JPEG quality 6", quality=6)
    im = pattern_bg(1400, 900, seed=3)
    ImageDraw.Draw(im).text((300, 500), "cocktails", fill=(190, 160, 120), font=font(2, 26))
    save(im, "h05_camouflaged.png", "positive", "low-contrast text on busy pattern")
    im = Image.new("RGB", (1600, 1000), (245, 245, 245))
    small = rotated_text("Cocktails", 90, size=18, w=400, h=400)
    im.paste(small, (1100, 450))
    ImageDraw.Draw(im).text((80, 80), "Weekly specials", fill=(0, 0, 0), font=font(1, 48))
    save(im, "h06_small_sideways.png", "positive", "18px sideways text")

    # ---- negatives ---------------------------------------------------------
    save(text_block(["Mocktail Menu", "Virgin mojito", "Shirley Temple"]), "n01_mocktail.png", "negative", "mocktail only")
    save(text_block([rnd.choice(FILLER) for _ in range(5)], fi=1), "n02_other_menu.jpg", "negative", "menu without target", quality=90)
    save(Image.new("RGB", (900, 600), (235, 235, 235)), "n03_blank.png", "negative", "blank image")
    save(text_block(["Cock and Bull pub", "Fox tail ale"], fi=2), "n04_partial_words.png", "negative", "similar words")
    save(pattern_bg(seed=2), "n05_pattern_no_text.png", "negative", "pattern without text")
    save(rotated_text("Wine and dine", 90), "n06_rotated_other.png", "negative", "rotated non-target text")
    save(text_block(["Coffee & Cake", "Opening hours 8-18"], fg=(240, 240, 240), bg=(30, 30, 30)), "n07_dark_other.png", "negative", "light-on-dark non-target")

    with open(out / "labels.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["filename", "label", "case"])
        w.writerows(rows)
    return rows


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "eval_dataset")
    rows = build(target)
    print(f"wrote {len(rows)} images to {target}")
