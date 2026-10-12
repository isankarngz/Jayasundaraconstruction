# Cocktail Finder: bulk image text search for "cocktail" / "cocktails"

A local web application that checks large image collections for the visible words **cocktail** or **cocktails**. Any capitalisation, font, size or orientation counts. Every image gets a traceable classification. Anything uncertain goes to a human reviewer, and an audit tracks the collection until every image is accounted for.

Everything runs on your own machine. No image is ever sent to an external service. Both OCR engines run locally, and the OCR models ship inside the Python package, so nothing is downloaded at runtime.

---

## 1. Architecture

```
Browser (React + TypeScript, Vite build)
   │  upload in batches · dashboard · results · contact sheet · review · audit · export
   ▼
FastAPI (Python)  ── REST API, static frontend, streaming uploads, exports
   │
   ├── SQLite (WAL)       jobs, images, every OCR pass, every hit, rejected uploads, event log
   ├── data/originals/    original files, stored unmodified under their SHA-256
   ├── data/derived/      thumbnails, browser-viewable copies of TIFF / EXIF-rotated images
   └── Worker pool (threads, CF_WORKERS)
          └── OCR pipeline
                ├── Engine 1: PaddleOCR PP-OCRv3 det/cls/rec models via ONNX Runtime (RapidOCR)
                └── Engine 2: Tesseract 5 LSTM (independent detector and recogniser, word-level boxes)
```

| Requirement | Choice | Notes |
|---|---|---|
| OCR | PaddleOCR PP-OCRv3 models **via ONNX Runtime** (`rapidocr_onnxruntime`) + **Tesseract 5** | These are the same PaddleOCR detection and recognition models, run without the PaddlePaddle framework. They install with `pip` on Linux, macOS and Windows and need no model download. Engines sit behind an `OcrEngine` interface (`backend/app/ocr/`), so you can add the full PaddleOCR package, EasyOCR or a cloud engine later. |
| Queue | In-process worker threads that claim work from SQLite | No Redis or Celery to install. The queue is persistent: a refresh, restart or crash loses nothing, and interrupted images are re-queued on start-up. |
| Database | SQLite | Right for a single-machine deployment. The SQL is plain enough to port to PostgreSQL for multi-user use. |
| Storage | Local disk | Originals are content-addressed (`sha256.ext`) and never modified. |

## 2. Accuracy: what this can and cannot promise

- **OCR is not proof.** Both engines can misread or entirely miss text: extreme stylisation, script fonts, handwriting, text blended into backgrounds, letters stacked vertically one per line, words hyphenated across two lines, very low resolution. Two engines agreeing on a negative is evidence, not proof.
- The application is therefore built so that **uncertainty becomes review, never a silent negative**. An image is only automatically classified **NO MATCH – VERIFIED** when all of the following hold:
  - it decoded correctly;
  - all ~17 OCR passes ran without error on **both** engines;
  - no exact hit, low-confidence hit, OCR-confusion candidate, near miss, merged word or cross-engine disagreement was found;
  - no text region stayed unreadable to both engines;
  - the image is not blank or heavily blurred, not tiny, and not a multi-page file.

  That is an *operational* verification standard, and the report says so.
- **Confidence scores never make an image "guaranteed".** Under the default **strict** audit policy, the collection is reported as fully verified only when a person has recorded a decision for **every** image. The **risk-based** policy accepts automated negatives, but the report then states explicitly that the result is *not* 100% human-verified.
- Measured results are in section 8. They show the pipeline works on the bundled test set; they are **not** a guarantee for your images. Section 8 also explains how to measure accuracy on a labelled sample of your own collection.

## 3. Processing pipeline (`backend/app/pipeline.py`)

| Stage | What runs | Engine |
|---|---|---|
| **A** | Original image at native resolution, with EXIF orientation applied | PaddleOCR |
| **B** | Preprocessing variants: 90°/270° rotations; ×2–3 upscale (small images) or overlapping ×2 tiles (large images); grayscale + CLAHE; HSV saturation; LAB chroma; median + CLAHE; adaptive binarisation; inversion; NL-means denoise + unsharp mask; contrast stretch / gamma; 180° rotation; alpha composited on black for transparent PNGs | PaddleOCR |
| **R** | Region re-OCR. Every candidate, every low-confidence possible match and every low-confidence text line is cropped, upscaled and re-read | **both** engines |
| **C** | Independent verification: original (`--psm 3`), Otsu-binarised upscale (`--psm 11`), both sideways rotations | Tesseract |

A confident exact match ends the run early. *Thorough mode* runs every pass anyway, which gives more complete occurrence counts. All bounding boxes are mapped back to original-image coordinates through each variant's inverse transform. Tesseract boxes are true word boxes. PaddleOCR gives line boxes, so the word's sub-box is interpolated from its character positions; these approximate boxes are drawn dashed and flagged in the CSV.

**Outcomes:** `MATCH CONFIRMED`, `NO MATCH – VERIFIED`, `REVIEW REQUIRED`. A processing failure (corrupt file, decode error, engine crash after retries) is always `REVIEW REQUIRED` and is also counted separately as a failure.

## 4. Word-matching rules (`backend/app/matching.py`)

| Kind | Example | Result |
|---|---|---|
| exact standalone word (case-insensitive, Unicode NFKC) | `Cocktail Menu`, `COCKTAILS!`, `Cocktail(s)`, `Non-alcoholic cocktails`, `cocktail-hour` | **match** |
| whitespace variation | `C O C K T A I L S`, `Cock tail` | **match** |
| OCR-confusion candidate | `cocktaiI`, `c0cktail`, `COCKTA1L` | review, never a match |
| near miss (edit distance ≤ 2) | `cocktai`, `coktails` | review |
| merged / compound word | `CocktailMenu`, `Cocktailling`, `cock-tail` | review (OCR often drops spaces) |
| excluded look-alike | `Mocktail(s)` | not a match. Recorded, and triggers review if another engine read "Cocktail" in the same place |

Translations and semantic equivalents are never matched. Candidate flagging (the last three rows) is the per-collection setting *"Flag OCR-confusion candidates"*. It is on by default; turning it off increases the risk of misses. Raw OCR text is stored separately from the normalised form.

## 5. Setup

### Option A: Docker (simplest)
```bash
cd cocktail-ocr
docker build -t cocktail-finder .
docker run --rm -p 127.0.0.1:8000:8000 -v "$PWD/data:/data" cocktail-finder
# open http://127.0.0.1:8000
```

### Option B: native install
Requirements: Python 3.10+, Node.js 18+ (only to build the UI), Tesseract 5.
```bash
# Tesseract:  Ubuntu/Debian: sudo apt install tesseract-ocr
#             macOS:         brew install tesseract
#             Windows:       https://github.com/UB-Mannheim/tesseract/wiki (add to PATH or set CF_TESSERACT_CMD)
cd cocktail-ocr
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
(cd frontend && npm ci && npm run build)
./run.sh                                                  # or: cd backend && uvicorn app.main:app
# open http://127.0.0.1:8000
```
Frontend development: run the backend, then `cd frontend && npm run dev` and open http://localhost:5173. Vite proxies `/api` to port 8000.

If Tesseract is missing, the app still runs. Images without a confident match then go to **review** instead of being classified negative, and the dashboard shows a warning.

### Configuration (environment variables)
| Variable | Default | Meaning |
|---|---|---|
| `CF_DATA_DIR` | `./data` | Database, originals, thumbnails, temporary exports |
| `CF_WORKERS` | `1` | Images processed in parallel. Each worker uses several CPU threads; 2 suits a 4–8 core machine. |
| `CF_MAX_FILE_MB` | `200` | Larger uploads are rejected (and listed as rejected) |
| `CF_MAX_MEGAPIXELS` | `150` | Decompression-bomb protection. Larger images become processing failures. |
| `CF_MAX_ATTEMPTS` | `2` | Automatic retries after unexpected errors |
| `CF_MATCH_CONFIDENCE` | `0.60` | An exact hit needs this recognition confidence to count as a confirmed match; below it the image goes to review |
| `CF_LOW_CONFIDENCE` | `0.50` | Lines below this confidence are re-read; if still unreadable, the image goes to review |
| `CF_BLUR_THRESHOLD` | `12` | Images below this Laplacian sharpness go to review |
| `CF_ENABLE_TESSERACT` / `CF_TESSERACT_CMD` | `1` / `tesseract` | Second engine |
| `CF_HOST` / `CF_PORT` | `127.0.0.1` / `8000` | Used by `run.sh`. Keep it on localhost unless you add authentication in front. |

## 6. Using it

1. **Create a collection.** Choose the audit policy (strict is recommended) and whether to flag OCR-confusion candidates.
2. **Upload.** Drag files or whole folders onto the drop zone, or use *Choose files…* / *Choose folder…*.
   - Files go up in batches of 20 (≤ 48 MB per request), so the browser never holds the whole collection in memory.
   - Relative folder paths and original filenames are kept.
   - Unsupported, empty or oversized files are listed as **rejected**, never silently dropped.
   - Identical content (same SHA-256) is recorded as a **duplicate** linked to the original. Different files with the same name are kept as separate images.
   - If an upload is interrupted, add the folder again and leave *Skip files already in this collection* ticked to resume.
3. **Dashboard.** Shows live progress and metrics. You can **pause or resume** processing, **retry failed** images, re-run review items, or re-run everything; human decisions are kept. Processing continues if you close the tab.
4. **Results.** A searchable, filterable, sortable table showing thumbnail, filename, classification, verification status, detected word, the OCR line around it, confidence, engine and preprocessing method, warnings and errors. Search covers filenames, hashes and OCR text near hits.
5. **Contact sheet.** A thumbnail grid with adjustable size and multi-select (click, shift-click, Ctrl+A) for quick triage and bulk decisions. Decisions made here are recorded with the method `contact_sheet`. Fine print is not legible in a thumbnail; open anything with small text in Review first.
6. **Review.** Shows the full-resolution image next to the OCR results. Detected words have boxes (green = match, amber = candidate, grey = look-alike). Click a detection to zoom to it.
   - Keyboard: `M` match · `N` no match · `U` unreadable · `X` clear · `←`/`→` previous/next · `H` next hit · `F` fit · `Z` 100% · `+`/`-` zoom · `B` boxes. Ctrl+wheel also zooms.
   - The queue defaults to *Unresolved (audit)*. Every pass, its OCR text and any errors are listed under *All OCR passes*.
7. **Audit.** A checklist of what still blocks completion: pending, failed, review-required, missing human decisions and rejected uploads, plus the list of unresolved images. The header shows a warning while anything is unresolved.
8. **Export.** All exports read the complete dataset from the server:
   - `all.csv`: every image with filename, relative path, SHA-256, final classification, verification status, automated result, human decision and method, detected words and per-word counts, best confidence, engines and methods, bounding boxes (JSON), surrounding OCR text, raw OCR text, pass count, reasons, warnings and errors.
   - `matches.csv`: matches only.
   - `matches.zip`: original, byte-identical files with original names (collisions become `name (2).ext`), plus `manifest.csv`.
   - `review.zip`: everything still marked review required, plus `manifest.csv`.
   - `report.html` / `report.json`: statistics, audit status, unresolved images, failures, rejected uploads and the match list.

   CSV cells are protected against spreadsheet formula injection.

## 7. Security

- The server binds to localhost by default, and there are no outbound network calls.
- Uploads are streamed to disk with a size limit and hashed. Files are stored under generated names, so client filenames never become paths. Path components such as `..` are stripped.
- Each file's type is checked by magic bytes and extension at upload. Before OCR the file is fully decoded with Pillow's decompression-bomb guard and an explicit pixel limit; a file that fails to decode becomes a processing failure.
- Download filenames are sanitised. Security headers are set (CSP, nosniff, frame-deny).
- To expose the app beyond localhost, put it behind a reverse proxy with authentication; it has no user accounts of its own.

## 8. Testing and measured results

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q                         # 104 tests, real OCR engines, about 1.5 min
python scripts/make_dataset.py ../eval_dataset
python scripts/evaluate.py ../eval_dataset  # precision / recall / silent misses
```

The tests cover:
- Matching rules: case, plural, punctuation, Unicode, mocktail, candidates.
- Geometry inverse transforms.
- Very small text, rotated and sideways text, upside-down text, skew, low contrast, blur, patterned and camouflaged backgrounds, light-on-dark text, letter-spacing, multiple occurrences, TIFF, WEBP.
- Duplicates and same-name files; corrupted, truncated, empty and unsupported files; decompression bombs; EXIF orientation; multi-frame TIFF.
- Scripted engines for each review trigger: low confidence, OCR-confusion candidates, engine disagreement, an engine crash, a missing second engine, unreadable regions.
- Pause, interrupted-processing recovery and retry.
- The review/audit state machine, complete CSV and ZIP exports with name collisions, the reports, CSV injection and path traversal.

**Bundled synthetic set (33 images, labels fixed by construction):**

| | |
|---|---|
| Positives automatically matched | 26 / 26 |
| False-positive matches | 0 / 7 negatives |
| **Silent misses** (positive → no match) | **0** |
| Negatives sent to review | 1 (the blank image) |

While building this set, one positive was initially a silent miss: low-contrast coloured text on a busy texture. That led to adding the HSV-saturation and LAB-chroma passes, and the image stays in the set as a regression test.

**These results are on rendered text, not on your photographs.** To estimate performance on your collection, look at a random sample of your own images, write `filename,label` (positive/negative) into a `labels.csv`, and run `scripts/evaluate.py` on it. With 0 misses in *N* positives, the 95% upper bound on the miss rate is still about 3/*N*. That is why the strict audit exists.

**Throughput (4-core CPU, 1 worker):** about 0.3–1 s for an image matched in Stage A, and about 6–30 s for a negative or uncertain image that goes through every stage. Large, noisy images are the slowest because of tiling and Tesseract. Increase `CF_WORKERS` on bigger machines.

## 9. Known limitations

- Only the first frame of a multi-page TIFF or animated WEBP is analysed. These files are always sent to review.
- Words split across two lines (`cock-` / `tail`), letters stacked vertically one per line, handwriting and decorative script fonts are weak spots for both engines. That is what the human review and strict audit are for.
- PaddleOCR word boxes are interpolated from the line box, so they are approximate.
- There are no user accounts or authentication. This is a single-operator local tool.
