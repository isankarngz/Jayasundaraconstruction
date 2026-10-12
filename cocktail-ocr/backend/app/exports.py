"""CSV / ZIP / report exports.  Every export reads the complete dataset from
the database - never just what the dashboard currently shows."""
from __future__ import annotations

import csv
import html
import io
import json
import os
import tempfile
import time
import zipfile
from pathlib import Path

from . import __version__, service
from .config import settings
from .db import Database

CSV_COLUMNS = [
    "image_id", "original_filename", "relative_path", "sha256", "final_classification",
    "verification_status", "automated_result", "human_decision", "review_method", "reviewed_at",
    "detected_words", "occurrences", "count_cocktail", "count_cocktails", "best_confidence",
    "match_engines", "match_methods", "bounding_boxes", "surrounding_text", "raw_ocr_text",
    "ocr_engines_run", "ocr_passes", "processing_status", "attempts", "duplicate_of",
    "width", "height", "format", "size_bytes", "reasons", "warnings", "error", "review_note",
]


def _cell(v) -> str:
    """Stringify and neutralise spreadsheet formula injection."""
    if v is None:
        return ""
    s = str(v)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        s = "'" + s
    return s


def _fmt_ts(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else ""


def image_rows(db: Database, job_id: int) -> list[dict]:
    imgs = db.query("SELECT * FROM images WHERE job_id=? ORDER BY id", (job_id,))
    by_id = {r["id"]: r for r in imgs}
    hits = db.query("SELECT h.* FROM hits h JOIN images i ON i.id=h.image_id WHERE i.job_id=? "
                    "ORDER BY h.image_id, h.occurrence, h.confidence DESC", (job_id,))
    hits_by: dict[int, list] = {}
    for h in hits:
        hits_by.setdefault(h["image_id"], []).append(h)
    passes = db.query("SELECT p.image_id, p.engine, p.variant, p.stage, p.status, p.text FROM passes p "
                      "JOIN images i ON i.id=p.image_id WHERE i.job_id=? ORDER BY p.image_id, p.seq", (job_id,))
    passes_by: dict[int, list] = {}
    for p in passes:
        passes_by.setdefault(p["image_id"], []).append(p)

    out = []
    for r in imgs:
        src = by_id.get(r["duplicate_of"]) if r["duplicate_of"] else r
        if src is None:
            src = r
        cls = service.classify(src)
        hs = hits_by.get(src["id"], [])
        best_per_occ: dict[int, object] = {}
        for h in hs:
            if h["kind"] in ("exact", "spaced") and h["occurrence"] is not None:
                cur = best_per_occ.get(h["occurrence"])
                if cur is None or (h["confidence"] or 0) > (cur["confidence"] or 0):
                    best_per_occ[h["occurrence"]] = h
        occ_hits = list(best_per_occ.values())
        ps = passes_by.get(src["id"], [])
        primary_text = next((p["text"] for p in ps if p["stage"] == "A"), "")
        wc = json.loads(src["word_counts_json"] or "{}")
        out.append({
            "image_id": r["id"],
            "original_filename": r["original_name"],
            "relative_path": r["rel_path"],
            "sha256": r["sha256"],
            "final_classification": cls,
            "verification_status": service.verification_status(src),
            "automated_result": (src["auto_result"] or ("error" if src["proc_status"] == "error" else src["proc_status"])),
            "human_decision": src["review_decision"] or "",
            "review_method": src["review_method"] or "",
            "reviewed_at": _fmt_ts(src["reviewed_at"]),
            "detected_words": src["words_found"] or "",
            "occurrences": src["occurrences"],
            "count_cocktail": wc.get("cocktail", 0),
            "count_cocktails": wc.get("cocktails", 0),
            "best_confidence": "" if src["best_confidence"] is None else f"{src['best_confidence']:.3f}",
            "match_engines": ";".join(sorted({h["engine"] for h in occ_hits})),
            "match_methods": ";".join(sorted({h["variant"] for h in occ_hits})),
            "bounding_boxes": json.dumps([{"word": h["raw"], "poly": json.loads(h["poly_json"]),
                                           "approximate": bool(h["box_approx"])} for h in occ_hits]),
            "surrounding_text": " | ".join(dict.fromkeys(h["line_text"] for h in occ_hits)),
            "raw_ocr_text": primary_text,
            "ocr_engines_run": ";".join(sorted({p["engine"] for p in ps})),
            "ocr_passes": len(ps),
            "processing_status": r["proc_status"],
            "attempts": src["attempts"],
            "duplicate_of": r["duplicate_of"] or "",
            "width": src["width"] or "",
            "height": src["height"] or "",
            "format": src["format"] or "",
            "size_bytes": r["size_bytes"],
            "reasons": " | ".join(json.loads(src["reasons_json"] or "[]")),
            "warnings": " | ".join(json.loads(src["warnings_json"] or "[]")),
            "error": src["error"] or "",
            "review_note": src["review_note"] or "",
            "_stored_name": r["stored_name"],
        })
    return out


def write_csv(rows: list[dict], path: Path) -> Path:
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_COLUMNS)
        for r in rows:
            w.writerow([_cell(r[c]) for c in CSV_COLUMNS])
    return path


def _tmp(job_id: int, suffix: str) -> Path:
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    fd, p = tempfile.mkstemp(dir=settings.exports_dir, prefix=f"job{job_id}-", suffix=suffix)
    os.close(fd)
    return Path(p)


def export_csv(db: Database, job_id: int, only: str | None = None) -> Path:
    rows = image_rows(db, job_id)
    if only == "matches":
        rows = [r for r in rows if r["final_classification"] == service.MATCH_CONFIRMED]
    return write_csv(rows, _tmp(job_id, ".csv"))


def unique_zip_name(name: str, used: set[str]) -> str:
    base, ext = os.path.splitext(service.safe_filename(name))
    cand = base + ext
    n = 2
    while cand.lower() in used:
        cand = f"{base} ({n}){ext}"
        n += 1
    used.add(cand.lower())
    return cand


def export_zip(db: Database, job_id: int, which: str) -> Path:
    """which: matches | review"""
    want = service.MATCH_CONFIRMED if which == "matches" else service.REVIEW_REQUIRED
    rows = [r for r in image_rows(db, job_id)
            if r["final_classification"] == want and r["processing_status"] != "duplicate"]
    path = _tmp(job_id, ".zip")
    used: set[str] = {"manifest.csv"}
    manifest = []
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for r in rows:
            src = service.stored_path(r["_stored_name"])
            arc = unique_zip_name(r["original_filename"], used)
            if src.exists():
                zf.write(src, arc)
                manifest.append({**r, "zip_entry": arc})
            else:
                manifest.append({**r, "zip_entry": "", "error": (r["error"] + " | stored file missing").strip(" |")})
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["zip_entry"] + CSV_COLUMNS)
        for r in manifest:
            w.writerow([_cell(r["zip_entry"])] + [_cell(r[c]) for c in CSV_COLUMNS])
        zf.writestr("manifest.csv", buf.getvalue().encode("utf-8-sig"))
    return path


def report_data(db: Database, job_id: int) -> dict:
    job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    summ = service.summary(db, job_id)
    rows = image_rows(db, job_id)
    policy = summ["policy"]
    imgs = {r["id"]: r for r in db.query("SELECT * FROM images WHERE job_id=?", (job_id,))}
    unresolved = [r for r in rows if r["processing_status"] != "duplicate"
                  and service.needs_human(imgs[r["image_id"]], policy)]
    failures = [r for r in rows if r["processing_status"] == "error"]
    rejected = [dict(x) for x in db.query("SELECT original_name, reason, at FROM rejected WHERE job_id=? ORDER BY id",
                                          (job_id,))]
    passes = db.one("SELECT COUNT(*) n, COALESCE(SUM(duration_ms),0) ms, SUM(status='error') errs FROM passes p "
                    "JOIN images i ON i.id=p.image_id WHERE i.job_id=?", (job_id,))
    engines = [dict(x) for x in db.query(
        "SELECT engine, COUNT(*) passes, SUM(status='error') errors FROM passes p JOIN images i ON i.id=p.image_id "
        "WHERE i.job_id=? GROUP BY engine", (job_id,))]
    return {
        "generated_at": _fmt_ts(time.time()),
        "app_version": __version__,
        "job": {"id": job["id"], "name": job["name"], "created_at": _fmt_ts(job["created_at"]),
                "settings": service.job_settings(db, job_id)},
        "summary": summ,
        "ocr": {"passes": passes["n"], "total_ocr_seconds": round((passes["ms"] or 0) / 1000, 1),
                "failed_passes": passes["errs"] or 0, "engines": engines},
        "unresolved": [{k: r[k] for k in ("image_id", "relative_path", "final_classification",
                                          "verification_status", "reasons", "error")} for r in unresolved],
        "failures": [{k: r[k] for k in ("image_id", "relative_path", "error")} for r in failures],
        "rejected_uploads": rejected,
        "matches": [{k: r[k] for k in ("image_id", "relative_path", "detected_words", "occurrences",
                                       "best_confidence", "verification_status")}
                    for r in rows if r["final_classification"] == service.MATCH_CONFIRMED
                    and r["processing_status"] != "duplicate"],
    }


def report_html(db: Database, job_id: int) -> str:
    d = report_data(db, job_id)
    s = d["summary"]
    e = html.escape

    def table(rows: list[dict], cols: list[str]) -> str:
        if not rows:
            return "<p class=muted>None.</p>"
        head = "".join(f"<th>{e(c.replace('_', ' '))}</th>" for c in cols)
        body = "".join("<tr>" + "".join(f"<td>{e(str(r.get(c, '')))}</td>" for c in cols) + "</tr>" for r in rows)
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    ok = s["strict_fully_verified"]
    banner_cls = "ok" if ok else ("warn" if s["audit_complete"] else "bad")
    metrics = [
        ("Unique images", s["total_unique"]), ("Duplicates (same file hash)", s["duplicates"]),
        ("Rejected at upload", s["rejected_uploads"]), ("Processed", s["processed"]),
        ("Not yet processed", s["pending"]), ("Processing failures", s["failed"]),
        ("Matches (final)", s["confirmed_matches"]), ("No match (final)", s["verified_negatives"]),
        ("Review required (final)", s["review_required"]), ("Human-reviewed", f"{s['human_reviewed']} ({s['reviewed_pct']}%)"),
        ("Marked unreadable by reviewer", s["human_unreadable"]),
        ("Occurrences: cocktail", s["occurrences"]["cocktail"]), ("Occurrences: cocktails", s["occurrences"]["cocktails"]),
        ("Unresolved under policy", s["unresolved"]),
    ]
    mt = "".join(f"<tr><th>{e(k)}</th><td>{e(str(v))}</td></tr>" for k, v in metrics)
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Verification report - {e(d['job']['name'])}</title>
<style>
body{{font:14px/1.5 system-ui,sans-serif;margin:24px auto;max-width:1100px;padding:0 16px;color:#1b1f24;background:#fff}}
h1{{font-size:22px}} h2{{font-size:17px;margin-top:28px;border-bottom:1px solid #ddd;padding-bottom:4px}}
table{{border-collapse:collapse;width:100%;margin:8px 0}} th,td{{border:1px solid #ddd;padding:4px 8px;text-align:left;vertical-align:top}}
th{{background:#f4f5f7}} .banner{{padding:12px 16px;border-radius:6px;font-weight:600}}
.ok{{background:#e3f6e8;color:#0d5323}} .warn{{background:#fff4d6;color:#6b4b00}} .bad{{background:#fde4e4;color:#8a1111}}
.muted{{color:#666}} td{{word-break:break-word}}
</style></head><body>
<h1>Cocktail text search - verification report</h1>
<p class=muted>Job #{d['job']['id']} "{e(d['job']['name'])}" &middot; generated {e(d['generated_at'])} &middot; app v{e(d['app_version'])}
&middot; audit policy: <b>{e(s['policy'])}</b> &middot; fuzzy candidates: {e(str(d['job']['settings'].get('fuzzy')))}
&middot; thorough mode: {e(str(d['job']['settings'].get('thorough')))}</p>
<div class="banner {banner_cls}">{e(s['audit_message'])}</div>
<h2>Statistics</h2><table>{mt}</table>
<p class=muted>OCR passes run: {d['ocr']['passes']} ({d['ocr']['failed_passes']} failed), total OCR time {d['ocr']['total_ocr_seconds']} s.
Engines: {e(', '.join(f"{x['engine']} ({x['passes']} passes)" for x in d['ocr']['engines']))}.</p>
<h2>Accuracy statement</h2>
<p>Automated results come from OCR, which can misread or miss text. A "no match" produced automatically means the image
completed every configured OCR pass on two engines with no target word, candidate, or unreadable text region - it is
an operational verification standard, not proof. Only images a person has reviewed are human-verified; the collection
is reported as fully verified only when every image has a human decision (strict policy).</p>
<h2>Unresolved images ({len(d['unresolved'])})</h2>{table(d['unresolved'], ['image_id', 'relative_path', 'final_classification', 'verification_status', 'reasons', 'error'])}
<h2>Processing failures ({len(d['failures'])})</h2>{table(d['failures'], ['image_id', 'relative_path', 'error'])}
<h2>Rejected uploads ({len(d['rejected_uploads'])})</h2>{table(d['rejected_uploads'], ['original_name', 'reason'])}
<h2>Matching images ({len(d['matches'])})</h2>{table(d['matches'], ['image_id', 'relative_path', 'detected_words', 'occurrences', 'best_confidence', 'verification_status'])}
</body></html>"""
