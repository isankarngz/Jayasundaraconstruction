"""FastAPI application: REST API + static frontend."""
from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import __version__, exports, imaging, service
from .config import settings
from .db import Database
from .worker import WorkerPool

logging.basicConfig(level=os.environ.get("CF_LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("cocktail.api")


class State:
    db: Database
    pool: WorkerPool


state = State()


def init(start_workers: bool | None = None) -> None:
    settings.ensure_dirs()
    state.db = Database(settings.db_path)
    state.pool = WorkerPool(state.db)
    if settings.autostart_workers if start_workers is None else start_workers:
        state.pool.start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not getattr(state, "db", None):
        init()
    yield
    state.pool.stop()


app = FastAPI(title="Cocktail Finder", version=__version__, lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Content-Security-Policy",
                            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'")
    return resp


# ------------------------------------------------------------------ helpers --

def _job(job_id: int):
    row = state.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not row:
        raise HTTPException(404, "job not found")
    return row


def _image(image_id: int):
    row = state.db.one("SELECT * FROM images WHERE id=?", (image_id,))
    if not row:
        raise HTTPException(404, "image not found")
    return row


def _job_out(row) -> dict:
    return {"id": row["id"], "name": row["name"], "created_at": row["created_at"], "state": row["state"],
            "settings": service.job_settings(state.db, row["id"])}


# --------------------------------------------------------------------- jobs --

class JobIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    fuzzy: bool = True
    thorough: bool = False
    audit_policy: Literal["strict", "risk_based"] = "strict"


class JobPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    state: Literal["running", "paused"] | None = None
    fuzzy: bool | None = None
    thorough: bool | None = None
    audit_policy: Literal["strict", "risk_based"] | None = None


@app.get("/api/health")
def health():
    from .ocr.registry import engine_status
    return {"version": __version__, "engines": engine_status(), "workers": state.pool.n,
            "limits": {"max_file_mb": settings.max_file_bytes // (1024 * 1024),
                       "max_megapixels": settings.max_pixels // 1_000_000},
            "thresholds": {"match_confidence": settings.match_confidence,
                           "low_confidence": settings.low_confidence,
                           "blur_threshold": settings.blur_threshold}}


@app.get("/api/jobs")
def list_jobs():
    return [{**_job_out(r), "summary": service.summary(state.db, r["id"])}
            for r in state.db.query("SELECT * FROM jobs ORDER BY id DESC")]


@app.post("/api/jobs", status_code=201)
def create_job(body: JobIn):
    s = {"fuzzy": body.fuzzy, "thorough": body.thorough, "audit_policy": body.audit_policy}
    with state.db.tx() as c:
        cur = c.execute("INSERT INTO jobs(name, created_at, settings_json) VALUES (?,?,?)",
                        (body.name.strip(), time.time(), json.dumps(s)))
        job_id = cur.lastrowid
        state.db.log("job_created", s, job_id, conn=c)
    return _job_out(_job(job_id))


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    return {**_job_out(_job(job_id)), "summary": service.summary(state.db, job_id)}


@app.patch("/api/jobs/{job_id}")
def patch_job(job_id: int, body: JobPatch):
    _job(job_id)
    s = service.job_settings(state.db, job_id)
    for k in ("fuzzy", "thorough", "audit_policy"):
        v = getattr(body, k)
        if v is not None:
            s[k] = v
    with state.db.tx() as c:
        if body.name:
            c.execute("UPDATE jobs SET name=? WHERE id=?", (body.name.strip(), job_id))
        if body.state:
            c.execute("UPDATE jobs SET state=? WHERE id=?", (body.state, job_id))
        c.execute("UPDATE jobs SET settings_json=? WHERE id=?", (json.dumps(s), job_id))
        state.db.log("job_updated", body.model_dump(exclude_none=True), job_id, conn=c)
    state.pool.poke()
    return get_job(job_id)


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int):
    _job(job_id)
    rows = state.db.query("SELECT id, stored_name FROM images WHERE job_id=?", (job_id,))
    with state.db.tx() as c:
        c.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    for r in rows:
        service.clear_derived(r["id"])
    service.remove_unreferenced_files(state.db, {r["stored_name"] for r in rows})
    return {"deleted": job_id}


# ------------------------------------------------------------------- upload --

@app.post("/api/jobs/{job_id}/upload")
async def upload(job_id: int, files: list[UploadFile] = File(...), paths: list[str] = Form(default=[])):
    _job(job_id)
    if len(files) > 500:
        raise HTTPException(413, "at most 500 files per request; the web UI uploads in batches")
    results = []
    for i, f in enumerate(files):
        rel = paths[i] if i < len(paths) else f.filename
        try:
            res = await run_in_threadpool(service.ingest_stream, state.db, job_id, f.filename or "unnamed",
                                          rel, f.file)
        except Exception as exc:  # never lose track of a file silently
            log.exception("ingest failed for %s", f.filename)
            res = service._reject(state.db, job_id, rel or "unnamed", f"upload failed: {exc}")
        finally:
            await f.close()
        results.append(res)
    state.pool.poke()
    return {"results": results,
            "accepted": sum(r["status"] == "accepted" for r in results),
            "duplicates": sum(r["status"] == "duplicate" for r in results),
            "rejected": sum(r["status"] == "rejected" for r in results)}


class CheckFile(BaseModel):
    path: str = Field(max_length=1000)
    size: int


class CheckIn(BaseModel):
    files: list[CheckFile] = Field(max_length=5000)


@app.post("/api/jobs/{job_id}/upload/check")
def upload_check(job_id: int, body: CheckIn):
    """Which of these (relative path, size) pairs are already in the job?  Lets
    the browser resume an interrupted folder upload without re-sending files."""
    _job(job_id)
    have = {(r["rel_path"], r["size_bytes"]) for r in
            state.db.query("SELECT rel_path, size_bytes FROM images WHERE job_id=?", (job_id,))}
    return {"present": [(service.clean_rel_path(f.path, f.path), f.size) in have for f in body.files]}


@app.get("/api/jobs/{job_id}/rejected")
def rejected(job_id: int):
    _job(job_id)
    return [dict(r) for r in state.db.query("SELECT * FROM rejected WHERE job_id=? ORDER BY id", (job_id,))]


# ------------------------------------------------------------------- images --

FILTERS = {
    "all": "1=1",
    "unique": "i.proc_status!='duplicate'",
    "match": f"({service.FINAL_SQL})='MATCH_CONFIRMED'",
    "no_match": f"({service.FINAL_SQL})='NO_MATCH_VERIFIED'",
    "review": f"({service.FINAL_SQL})='REVIEW_REQUIRED'",
    "failed": "i.proc_status='error'",
    "pending": "i.proc_status IN ('queued','processing')",
    "duplicate": "i.proc_status='duplicate'",
    "unreviewed": "i.proc_status!='duplicate' AND i.review_decision IS NULL",
    "reviewed": "i.review_decision IS NOT NULL",
    "auto_match": "i.auto_result='match'",
    "auto_review": "i.auto_result='review' OR i.proc_status='error'",
    "auto_no_match": "i.auto_result='no_match'",
}
SORTS = {
    "id": "i.id", "filename": "i.rel_path COLLATE NOCASE", "status": "i.proc_status",
    "classification": "final", "verification": "verification", "confidence": "COALESCE(i.best_confidence,-1)",
    "occurrences": "i.occurrences", "size": "i.size_bytes",
}


def _unresolved_clause(job_id: int) -> str:
    policy = service.job_settings(state.db, job_id).get("audit_policy", "strict")
    base = "i.proc_status!='duplicate' AND (i.review_decision IS NULL OR i.review_decision='unreadable')"
    if policy == "strict":
        return base
    return base + " AND NOT (i.proc_status='done' AND i.auto_result='no_match')"


def _image_out(r) -> dict:
    d = {k: r[k] for k in ("id", "job_id", "sha256", "original_name", "rel_path", "format", "size_bytes",
                            "width", "height", "proc_status", "auto_result", "error", "attempts",
                            "best_confidence", "occurrences", "words_found", "review_decision", "review_method",
                            "review_note", "reviewed_at", "duplicate_of", "finished_at", "sharpness")}
    d["reasons"] = json.loads(r["reasons_json"] or "[]")
    d["warnings"] = json.loads(r["warnings_json"] or "[]")
    d["word_counts"] = json.loads(r["word_counts_json"] or "{}")
    d["final"] = r["final"] if "final" in r.keys() else service.classify(r)
    d["verification"] = r["verification"] if "verification" in r.keys() else service.verification_status(r)
    return d


@app.get("/api/jobs/{job_id}/images")
def list_images(job_id: int, filter: str = "all", q: str = "", sort: str = "id",
                order: Literal["asc", "desc"] = "asc", offset: int = Query(0, ge=0),
                limit: int = Query(100, ge=1, le=1000), ids_only: bool = False):
    _job(job_id)
    where = ["i.job_id=?"]
    args: list = [job_id]
    if filter == "unresolved":
        where.append(_unresolved_clause(job_id))
    else:
        if filter not in FILTERS:
            raise HTTPException(400, f"unknown filter {filter}")
        where.append(FILTERS[filter])
    if q:
        where.append("(i.rel_path LIKE ? OR i.sha256 LIKE ? OR EXISTS (SELECT 1 FROM hits h WHERE h.image_id="
                     "COALESCE(i.duplicate_of,i.id) AND h.line_text LIKE ?))")
        args += [f"%{q}%", f"{q}%", f"%{q}%"]
    order_sql = SORTS.get(sort, "i.id") + (" DESC" if order == "desc" else " ASC") + ", i.id ASC"
    base = (f"FROM images i WHERE {' AND '.join(where)}")
    total = state.db.one(f"SELECT COUNT(*) n {base}", args)["n"]
    if ids_only:
        rows = state.db.query(f"SELECT i.id, ({service.FINAL_SQL}) final, ({service.VERIFICATION_SQL}) verification "
                              f"{base} ORDER BY {order_sql}", args)
        return {"total": total, "ids": [r["id"] for r in rows]}
    rows = state.db.query(f"SELECT i.*, ({service.FINAL_SQL}) final, ({service.VERIFICATION_SQL}) verification "
                          f"{base} ORDER BY {order_sql} LIMIT ? OFFSET ?", args + [limit, offset])
    items = [_image_out(r) for r in rows]
    # attach the best match hit + context for gallery cards
    ids = [it["duplicate_of"] or it["id"] for it in items]
    if ids:
        marks = ",".join("?" * len(ids))
        hits = state.db.query(f"SELECT image_id, kind, raw, line_text, confidence, engine, variant FROM hits "
                              f"WHERE image_id IN ({marks}) AND kind!='excluded' "
                              f"ORDER BY (kind IN ('exact','spaced')) DESC, confidence DESC", ids)
        first: dict[int, dict] = {}
        for h in hits:
            first.setdefault(h["image_id"], dict(h))
        for it in items:
            it["top_hit"] = first.get(it["duplicate_of"] or it["id"])
    return {"total": total, "items": items}


@app.get("/api/images/{image_id}")
def image_detail(image_id: int):
    r = _image(image_id)
    src_id = r["duplicate_of"] or image_id
    src = _image(src_id)
    out = _image_out(r)
    if r["duplicate_of"]:
        out["final"] = service.classify(src)
        out["verification"] = service.verification_status(src)
    out["hits"] = [{**dict(h), "poly": json.loads(h["poly_json"])} for h in
                   state.db.query("SELECT * FROM hits WHERE image_id=? ORDER BY occurrence, confidence DESC", (src_id,))]
    for h in out["hits"]:
        h.pop("poly_json", None)
    out["passes"] = [{**{k: p[k] for k in ("id", "seq", "stage", "engine", "variant", "duration_ms", "status",
                                           "error", "n_lines", "mean_conf", "text")},
                      "lines": json.loads(p["lines_json"]) if p["stage"] in ("A",) else None}
                     for p in state.db.query("SELECT * FROM passes WHERE image_id=? ORDER BY seq", (src_id,))]
    out["duplicates"] = [x["id"] for x in state.db.query("SELECT id FROM images WHERE duplicate_of=?", (src_id,))]
    out["same_name"] = [x["id"] for x in state.db.query(
        "SELECT id FROM images WHERE job_id=? AND original_name=? AND id!=?", (r["job_id"], r["original_name"], image_id))]
    if r["duplicate_of"]:
        out["inherited"] = _image_out(src)
    return out


@app.get("/api/images/{image_id}/passes/{pass_id}")
def pass_detail(image_id: int, pass_id: int):
    p = state.db.one("SELECT * FROM passes WHERE id=? AND image_id=?", (pass_id, image_id))
    if not p:
        raise HTTPException(404, "pass not found")
    return {**{k: p[k] for k in p.keys() if k != "lines_json"}, "lines": json.loads(p["lines_json"])}


_MIME = {"JPEG": "image/jpeg", "MPO": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "BMP": "image/bmp",
         "TIFF": "image/tiff"}


@app.get("/api/images/{image_id}/thumb")
def thumb(image_id: int):
    r = _image(image_id)
    p = service.derived_path(r["duplicate_of"] or image_id, "thumb")
    if not p.exists():
        raise HTTPException(404, "thumbnail not available")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/images/{image_id}/view")
def view(image_id: int):
    """Full-resolution image in the same orientation as the OCR coordinates."""
    r = _image(image_id)
    src = service.stored_path(r["stored_name"])
    if not src.exists():
        raise HTTPException(404, "stored file missing")
    fmt = r["format"]
    needs_convert = fmt not in imaging.WEB_FORMATS
    if not needs_convert:
        try:
            from PIL import Image
            with Image.open(src) as im:
                needs_convert = im.getexif().get(0x0112, 1) not in (1, None)
        except Exception:
            needs_convert = True
    if not needs_convert:
        return FileResponse(src, media_type=_MIME.get(fmt, "application/octet-stream"),
                            headers={"Cache-Control": "private, max-age=3600"})
    cached = service.derived_path(r["duplicate_of"] or image_id, "view")
    if not cached.exists():
        try:
            loaded = imaging.load_image(src.read_bytes(), settings.max_pixels)
        except imaging.ImageLoadError as exc:
            raise HTTPException(422, f"image cannot be decoded: {exc}")
        cached.write_bytes(imaging.make_view(loaded.rgb))
    return FileResponse(cached, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/images/{image_id}/original")
def original(image_id: int):
    r = _image(image_id)
    src = service.stored_path(r["stored_name"])
    if not src.exists():
        raise HTTPException(404, "stored file missing")
    return FileResponse(src, media_type="application/octet-stream",
                        filename=service.safe_filename(r["original_name"]))


class ReviewIn(BaseModel):
    decision: Literal["match", "no_match", "unreadable"] | None
    method: Literal["full_view", "contact_sheet", "bulk"] = "full_view"
    note: str | None = Field(default=None, max_length=2000)


class BulkReviewIn(ReviewIn):
    ids: list[int] = Field(min_length=1, max_length=5000)


def _apply_review(c, image_id: int, body: ReviewIn) -> None:
    c.execute("UPDATE images SET review_decision=?, review_method=?, review_note=?, reviewed_at=? "
              "WHERE id=? OR duplicate_of=?",
              (body.decision, body.method if body.decision else None, body.note,
               time.time() if body.decision else None, image_id, image_id))


@app.post("/api/images/{image_id}/review")
def review(image_id: int, body: ReviewIn):
    r = _image(image_id)
    target = r["duplicate_of"] or image_id
    with state.db.tx() as c:
        _apply_review(c, target, body)
        state.db.log("review", body.model_dump(), r["job_id"], target, conn=c)
    return image_detail(image_id)


@app.post("/api/jobs/{job_id}/review-bulk")
def review_bulk(job_id: int, body: BulkReviewIn):
    _job(job_id)
    n = 0
    with state.db.tx() as c:
        for iid in body.ids:
            r = c.execute("SELECT id, duplicate_of FROM images WHERE id=? AND job_id=?", (iid, job_id)).fetchone()
            if not r:
                continue
            _apply_review(c, r["duplicate_of"] or r["id"], body)
            n += 1
        state.db.log("review_bulk", {"n": n, **body.model_dump(exclude={"ids"})}, job_id, conn=c)
    return {"updated": n}


@app.post("/api/images/{image_id}/retry")
def retry(image_id: int):
    r = _image(image_id)
    if r["proc_status"] in ("duplicate", "processing"):
        raise HTTPException(409, f"cannot retry an image that is {r['proc_status']}")
    with state.db.tx() as c:
        c.execute("UPDATE images SET proc_status='queued', attempts=0, error=NULL WHERE id=?", (image_id,))
        state.db.log("retry_requested", None, r["job_id"], image_id, conn=c)
    state.pool.poke()
    return image_detail(image_id)


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: int, scope: Literal["failed", "review", "all"] = "failed"):
    _job(job_id)
    cond = {"failed": "proc_status='error'",
            "review": "(proc_status='error' OR auto_result='review')",
            "all": "proc_status IN ('done','error')"}[scope]
    with state.db.tx() as c:
        n = c.execute(f"UPDATE images SET proc_status='queued', attempts=0, error=NULL "
                      f"WHERE job_id=? AND {cond}", (job_id,)).rowcount
        state.db.log("retry_requested", {"scope": scope, "n": n}, job_id, conn=c)
    state.pool.poke()
    return {"requeued": n}


@app.get("/api/jobs/{job_id}/events")
def events(job_id: int, limit: int = Query(200, le=2000)):
    _job(job_id)
    return [dict(r) for r in state.db.query("SELECT * FROM events WHERE job_id=? ORDER BY id DESC LIMIT ?",
                                            (job_id, limit))]


# ------------------------------------------------------------------ exports --

def _cleanup(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


@app.get("/api/jobs/{job_id}/export/{kind}")
async def export(job_id: int, kind: Literal["all.csv", "matches.csv", "matches.zip", "review.zip",
                                          "report.html", "report.json"], background: BackgroundTasks):
    job = _job(job_id)
    stem = service.safe_filename(job["name"], "job").replace(" ", "_")
    stamp = time.strftime("%Y%m%d-%H%M")
    if kind == "report.json":
        return JSONResponse(await run_in_threadpool(exports.report_data, state.db, job_id))
    if kind == "report.html":
        html = await run_in_threadpool(exports.report_html, state.db, job_id)
        return HTMLResponse(html, headers={
            "Content-Disposition": f'attachment; filename="{stem}-verification-report-{stamp}.html"'})
    if kind.endswith(".csv"):
        path = await run_in_threadpool(exports.export_csv, state.db, job_id,
                                       "matches" if kind == "matches.csv" else None)
        media = "text/csv"
    else:
        path = await run_in_threadpool(exports.export_zip, state.db, job_id, kind.split(".")[0])
        media = "application/zip"
    background.add_task(_cleanup, path)
    names = {"all.csv": "all-images", "matches.csv": "matches", "matches.zip": "matching-images",
             "review.zip": "review-required-images"}
    return FileResponse(path, media_type=media, filename=f"{stem}-{names[kind]}-{stamp}.{kind.split('.')[-1]}",
                        background=background)


# ----------------------------------------------------------------- frontend --

if settings.frontend_dir.exists():
    if (settings.frontend_dir / "assets").exists():
        app.mount("/assets", StaticFiles(directory=settings.frontend_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = (settings.frontend_dir / path).resolve()
        if path and candidate.is_file() and settings.frontend_dir.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(settings.frontend_dir / "index.html")
else:
    @app.get("/", include_in_schema=False)
    def no_frontend():
        return Response("Frontend not built. Run `npm install && npm run build` in cocktail-ocr/frontend, "
                        "or use the API at /docs.", media_type="text/plain")
