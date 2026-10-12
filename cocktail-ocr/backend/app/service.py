"""Ingest, classification and audit logic shared by the API, the worker and
the exports."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import BinaryIO

from PIL import Image

from . import imaging
from .config import settings
from .db import Database

# ------------------------------------------------------------ classifications

MATCH_CONFIRMED = "MATCH_CONFIRMED"
NO_MATCH_VERIFIED = "NO_MATCH_VERIFIED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
PENDING = "PENDING"
DUPLICATE = "DUPLICATE"

FINAL_SQL = """CASE
 WHEN i.proc_status='duplicate' THEN 'DUPLICATE'
 WHEN i.review_decision='match' THEN 'MATCH_CONFIRMED'
 WHEN i.review_decision='no_match' THEN 'NO_MATCH_VERIFIED'
 WHEN i.review_decision='unreadable' THEN 'REVIEW_REQUIRED'
 WHEN i.proc_status IN ('queued','processing') THEN 'PENDING'
 WHEN i.proc_status='error' THEN 'REVIEW_REQUIRED'
 WHEN i.auto_result='match' THEN 'MATCH_CONFIRMED'
 WHEN i.auto_result='no_match' THEN 'NO_MATCH_VERIFIED'
 ELSE 'REVIEW_REQUIRED' END"""

VERIFICATION_SQL = """CASE
 WHEN i.review_decision IN ('match','no_match') THEN 'human_verified'
 WHEN i.review_decision='unreadable' THEN 'human_unresolved'
 WHEN i.proc_status IN ('queued','processing') THEN 'pending'
 ELSE 'automated_only' END"""

AUDIT_POLICIES = ("strict", "risk_based")
DEFAULT_JOB_SETTINGS = {"fuzzy": True, "thorough": False, "audit_policy": "strict"}


def classify(row) -> str:
    """Python mirror of FINAL_SQL (for rows that are not duplicates)."""
    if row["review_decision"] == "match":
        return MATCH_CONFIRMED
    if row["review_decision"] == "no_match":
        return NO_MATCH_VERIFIED
    if row["review_decision"] == "unreadable":
        return REVIEW_REQUIRED
    if row["proc_status"] in ("queued", "processing"):
        return PENDING
    if row["proc_status"] == "error":
        return REVIEW_REQUIRED
    return {"match": MATCH_CONFIRMED, "no_match": NO_MATCH_VERIFIED}.get(row["auto_result"], REVIEW_REQUIRED)


def verification_status(row) -> str:
    if row["review_decision"] in ("match", "no_match"):
        return "human_verified"
    if row["review_decision"] == "unreadable":
        return "human_unresolved"
    if row["proc_status"] in ("queued", "processing"):
        return "pending"
    return "automated_only"


def needs_human(row, policy: str) -> bool:
    """True when this (non-duplicate) image is still unresolved under the
    job's audit policy."""
    if row["review_decision"] in ("match", "no_match"):
        return False
    if policy == "strict":
        return True
    # risk_based: automated negatives are accepted, everything else needs a person.
    return not (row["proc_status"] == "done" and row["auto_result"] == "no_match")


# ------------------------------------------------------------------ ingest --

_SAFE = re.compile(r"[^A-Za-z0-9._ ()+-]")


def safe_filename(name: str, fallback: str = "image") -> str:
    """Basename suitable for a ZIP entry / download header."""
    name = unicodedata.normalize("NFKC", name).replace("\\", "/").split("/")[-1]
    name = _SAFE.sub("_", name).strip(" .")
    return name[:180] or fallback


def clean_rel_path(path: str | None, fallback: str) -> str:
    if not path:
        return fallback
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    cleaned = "/".join(parts)[:500]
    return cleaned or fallback


def stored_path(stored_name: str) -> Path:
    return settings.originals_dir / stored_name[:2] / stored_name


def ingest_stream(db: Database, job_id: int, filename: str, rel_path: str | None,
                  stream: BinaryIO) -> dict:
    """Stream one upload to disk (hashing as we go), validate it and record
    it.  Returns {"status": "accepted"|"duplicate"|"rejected", ...}."""
    original_name = (filename or "unnamed").replace("\\", "/").split("/")[-1][:255] or "unnamed"
    rel = clean_rel_path(rel_path, original_name)
    ext = os.path.splitext(original_name)[1].lower()
    sha = hashlib.sha256()
    size = 0
    settings.originals_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=settings.originals_dir, prefix=".upload-")
    head = b""
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_file_bytes:
                    return _reject(db, job_id, rel, f"file exceeds {settings.max_file_bytes // (1024 * 1024)} MB limit")
                if len(head) < 16:
                    head += chunk[:16]
                sha.update(chunk)
                out.write(chunk)
        if size == 0:
            return _reject(db, job_id, rel, "empty file")
        sniffed = imaging.sniff_format(head)
        if ext not in imaging.ALLOWED_EXTENSIONS and sniffed is None:
            return _reject(db, job_id, rel, f"unsupported file type '{ext or 'no extension'}'"
                           " (accepted: JPG, JPEG, PNG, WEBP, BMP, TIFF)")
        if sniffed is None and ext in imaging.ALLOWED_EXTENSIONS:
            # Keep it: it is accounted for and will be reported as a
            # processing failure (corrupt / mislabelled), never as negative.
            pass
        digest = sha.hexdigest()
        store_ext = ext if ext in imaging.ALLOWED_EXTENSIONS else "." + (sniffed or "bin").lower()
        stored_name = digest + store_ext
        dest = stored_path(stored_name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            os.unlink(tmp)
        else:
            os.replace(tmp, dest)
            os.chmod(dest, 0o640)
        tmp = None
        fmt, width, height = None, None, None
        try:
            with Image.open(dest) as im:  # header only - full decode happens in the worker
                fmt, (width, height) = im.format, im.size
        except Exception:
            pass
        with db.tx() as c:
            first = c.execute("SELECT id FROM images WHERE job_id=? AND sha256=? AND duplicate_of IS NULL "
                              "ORDER BY id LIMIT 1", (job_id, digest)).fetchone()
            status = "duplicate" if first else "queued"
            cur = c.execute(
                "INSERT INTO images(job_id, sha256, original_name, rel_path, ext, format, size_bytes, width, height,"
                " uploaded_at, stored_name, duplicate_of, proc_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, digest, original_name, rel, ext, fmt, size, width, height, time.time(), stored_name,
                 first["id"] if first else None, status))
            image_id = cur.lastrowid
            db.log("upload", {"name": rel, "sha256": digest, "status": status}, job_id, image_id, conn=c)
        return {"status": "duplicate" if first else "accepted", "id": image_id, "name": rel,
                "sha256": digest, "size": size, "width": width, "height": height,
                "duplicate_of": first["id"] if first else None}
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def _reject(db: Database, job_id: int, name: str, reason: str) -> dict:
    with db.tx() as c:
        c.execute("INSERT INTO rejected(job_id, original_name, reason, at) VALUES (?,?,?,?)",
                  (job_id, name, reason, time.time()))
        db.log("rejected", {"name": name, "reason": reason}, job_id, conn=c)
    return {"status": "rejected", "name": name, "reason": reason}


# ----------------------------------------------------------------- summary --

def job_settings(db: Database, job_id: int) -> dict:
    row = db.one("SELECT settings_json FROM jobs WHERE id=?", (job_id,))
    s = dict(DEFAULT_JOB_SETTINGS)
    if row:
        s.update(json.loads(row["settings_json"] or "{}"))
    return s


def summary(db: Database, job_id: int) -> dict:
    policy = job_settings(db, job_id).get("audit_policy", "strict")
    rows = db.query("SELECT id, proc_status, auto_result, review_decision, occurrences, word_counts_json "
                    "FROM images WHERE job_id=?", (job_id,))
    uniq = [r for r in rows if r["proc_status"] != "duplicate"]
    by_class = {MATCH_CONFIRMED: 0, NO_MATCH_VERIFIED: 0, REVIEW_REQUIRED: 0, PENDING: 0}
    counts = {"cocktail": 0, "cocktails": 0}
    human_matches_without_count = 0
    unresolved = 0
    reviewed = 0
    unreadable = 0
    for r in uniq:
        cls = classify(r)
        by_class[cls] += 1
        if cls == MATCH_CONFIRMED:
            wc = json.loads(r["word_counts_json"] or "{}")
            for k in counts:
                counts[k] += int(wc.get(k, 0))
            if not r["occurrences"]:
                human_matches_without_count += 1
        if r["review_decision"]:
            reviewed += 1
        if r["review_decision"] == "unreadable":
            unreadable += 1
        if needs_human(r, policy):
            unresolved += 1
    total = len(uniq)
    pending = sum(1 for r in uniq if r["proc_status"] in ("queued", "processing"))
    processing = sum(1 for r in uniq if r["proc_status"] == "processing")
    failed = sum(1 for r in uniq if r["proc_status"] == "error")
    processed_ok = sum(1 for r in uniq if r["proc_status"] == "done")
    auto = {k: sum(1 for r in uniq if r["proc_status"] == "done" and r["auto_result"] == k)
            for k in ("match", "no_match", "review")}
    rejected = db.one("SELECT COUNT(*) n FROM rejected WHERE job_id=?", (job_id,))["n"]
    dups = len(rows) - total
    processing_complete = total > 0 and pending == 0
    audit_complete = processing_complete and unresolved == 0
    strict_verified = (processing_complete and total > 0 and
                       all(r["review_decision"] in ("match", "no_match") for r in uniq))
    if total == 0:
        audit_message = "No images uploaded yet."
    elif not processing_complete:
        audit_message = f"Processing in progress: {pending} image(s) not yet analysed."
    elif strict_verified:
        audit_message = ("Every uploaded image has been individually verified by a human reviewer. "
                         "The classification of this collection is fully human-verified.")
    elif audit_complete:
        audit_message = ("All images are accounted for under the risk-based policy: every match, "
                         "review item and failure was human-checked, but "
                         f"{by_class[NO_MATCH_VERIFIED] - sum(1 for r in uniq if r['review_decision'] == 'no_match')} "
                         "negative(s) rely on automated OCR verification only. This is NOT a 100% "
                         "human-verified result.")
    else:
        audit_message = (f"{unresolved} image(s) remain unresolved under the '{policy}' audit policy. "
                         "The result must not be treated as complete.")
    return {
        "policy": policy,
        "total_unique": total,
        "duplicates": dups,
        "rejected_uploads": rejected,
        "processed": processed_ok + failed,
        "processed_ok": processed_ok,
        "pending": pending,
        "processing": processing,
        "failed": failed,
        "auto": auto,
        "confirmed_matches": by_class[MATCH_CONFIRMED],
        "verified_negatives": by_class[NO_MATCH_VERIFIED],
        "review_required": by_class[REVIEW_REQUIRED],
        "human_reviewed": reviewed,
        "human_unreadable": unreadable,
        "reviewed_pct": round(100.0 * reviewed / total, 1) if total else 0.0,
        "occurrences": {**counts, "total": counts["cocktail"] + counts["cocktails"],
                        "human_confirmed_matches_without_ocr_count": human_matches_without_count},
        "unresolved": unresolved,
        "processing_complete": processing_complete,
        "audit_complete": audit_complete,
        "strict_fully_verified": strict_verified,
        "audit_message": audit_message,
    }


def remove_unreferenced_files(db: Database, stored_names: set[str]) -> None:
    for name in stored_names:
        if db.one("SELECT 1 FROM images WHERE stored_name=? LIMIT 1", (name,)) is None:
            p = stored_path(name)
            if p.exists():
                p.unlink()


def derived_path(image_id: int, kind: str) -> Path:
    return settings.derived_dir / f"{image_id}_{kind}.jpg"


def clear_derived(image_id: int) -> None:
    for kind in ("thumb", "view"):
        p = derived_path(image_id, kind)
        if p.exists():
            p.unlink()

