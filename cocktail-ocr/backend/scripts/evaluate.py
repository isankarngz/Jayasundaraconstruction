"""Measure detection quality against a labelled image folder.

    python scripts/evaluate.py DATASET_DIR [--labels labels.csv] [--thorough] [--out results.csv]

labels.csv needs columns: filename,label   (label = positive | negative)

Reports precision / recall of automatic MATCH, and - the number that matters
most - SILENT MISSES: positive images the pipeline classified as NO MATCH.
Positives sent to REVIEW are not silent misses (a person will look at them),
but the review rate is reported so you can judge the manual workload.

The bundled synthetic set (scripts/make_dataset.py) only shows the pipeline
works on rendered text. To estimate performance on YOUR collection, label a
random sample of your own images (by looking at each one) and run this script
on that sample. A small sample cannot prove a low miss rate: with 0 misses in
N positives the 95% upper bound on the miss rate is about 3/N.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import imaging, pipeline  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", type=Path)
    ap.add_argument("--labels", type=Path)
    ap.add_argument("--thorough", action="store_true")
    ap.add_argument("--no-fuzzy", action="store_true")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    labels_path = a.labels or a.dataset / "labels.csv"
    rows = list(csv.DictReader(open(labels_path, newline="", encoding="utf-8-sig")))
    opts = pipeline.Options(fuzzy=not a.no_fuzzy, thorough=a.thorough)
    results = []
    for r in rows:
        t0 = time.time()
        try:
            res = pipeline.analyze((a.dataset / r["filename"]).read_bytes(), opts)
            status, reasons = res.status, res.reasons
        except imaging.ImageLoadError as exc:
            status, reasons = "error", [str(exc)]
        dt = time.time() - t0
        results.append({**r, "status": status, "seconds": round(dt, 2), "reason": reasons[0] if reasons else ""})
        print(f"{r['filename']:42s} {r['label']:8s} -> {status:8s} {dt:6.1f}s  {reasons[0][:90] if reasons else ''}",
              flush=True)

    pos = [x for x in results if x["label"] == "positive"]
    neg = [x for x in results if x["label"] == "negative"]
    tp = sum(x["status"] == "match" for x in pos)
    fp = sum(x["status"] == "match" for x in neg)
    fn_review = sum(x["status"] in ("review", "error") for x in pos)
    silent = [x for x in pos if x["status"] == "no_match"]
    neg_review = sum(x["status"] in ("review", "error") for x in neg)
    tn = sum(x["status"] == "no_match" for x in neg)
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / len(pos) if pos else float("nan")
    rec_esc = (tp + fn_review) / len(pos) if pos else float("nan")
    print("\n=== Summary ===")
    print(f"images: {len(results)}  positives: {len(pos)}  negatives: {len(neg)}")
    print(f"auto MATCH  : TP={tp}  FP={fp}   precision={prec:.3f}")
    print(f"recall (auto MATCH only)              = {rec:.3f}")
    print(f"recall incl. escalation to REVIEW     = {rec_esc:.3f}")
    print(f"SILENT MISSES (positive -> NO MATCH)  = {len(silent)}  {[x['filename'] for x in silent]}")
    print(f"false negatives sent to review        = {fn_review}")
    print(f"negatives: NO MATCH={tn}  REVIEW={neg_review}  (false-positive MATCH={fp})")
    print(f"review rate overall = {(fn_review + neg_review) / max(1, len(results)):.1%}  "
          f"mean time/image = {sum(x['seconds'] for x in results) / max(1, len(results)):.1f}s")
    if a.out:
        with open(a.out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(results[0]))
            w.writeheader()
            w.writerows(results)
    return 1 if silent else 0


if __name__ == "__main__":
    raise SystemExit(main())
