"""Background processing: a pool of worker threads that claim queued images
from SQLite, run the OCR pipeline and persist every pass and hit.

* Pause/resume is per job (paused jobs are simply not claimed from).
* Interrupted work (crash, restart) is re-queued on start-up.
* Unexpected exceptions are retried up to CF_MAX_ATTEMPTS, then recorded as
  a processing failure - which is always REVIEW REQUIRED, never negative.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import traceback

from . import imaging, pipeline, service
from .config import settings
from .db import Database
from .ocr.base import EngineUnavailable

log = logging.getLogger("cocktail.worker")


class WorkerPool:
    def __init__(self, db: Database, workers: int | None = None):
        self.db = db
        self.n = max(1, workers or settings.workers)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------ lifecycle
    def recover(self) -> int:
        with self.db.tx() as c:
            n = c.execute("UPDATE images SET proc_status='queued' WHERE proc_status='processing'").rowcount
            if n:
                self.db.log("recovered", f"{n} interrupted image(s) re-queued", conn=c)
        if n:
            log.warning("re-queued %d image(s) interrupted by a previous shutdown", n)
        return n

    def start(self) -> None:
        self.recover()
        self._stop.clear()
        for i in range(self.n):
            t = threading.Thread(target=self._loop, name=f"ocr-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        for t in self._threads:
            t.join(timeout)
        self._threads.clear()

    def poke(self) -> None:
        self._wake.set()

    # ----------------------------------------------------------------- work
    def claim(self):
        with self.db.tx() as c:
            row = c.execute(
                "SELECT i.id FROM images i JOIN jobs j ON j.id=i.job_id "
                "WHERE i.proc_status='queued' AND j.state='running' ORDER BY i.id LIMIT 1").fetchone()
            if not row:
                return None
            c.execute("UPDATE images SET proc_status='processing', started_at=?, attempts=attempts+1 "
                      "WHERE id=?", (time.time(), row["id"]))
            return c.execute("SELECT * FROM images WHERE id=?", (row["id"],)).fetchone()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                row = self.claim()
            except Exception:  # pragma: no cover - db busy etc.
                log.exception("claim failed")
                row = None
            if row is None:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            self.process(row)

    def process(self, row) -> None:
        image_id, job_id = row["id"], row["job_id"]
        t0 = time.time()
        try:
            data = service.stored_path(row["stored_name"]).read_bytes()
            loaded = imaging.load_image(data, settings.max_pixels)
            thumb = imaging.make_thumbnail(loaded.rgb)
            service.derived_path(image_id, "thumb").write_bytes(thumb)
            opts = pipeline.Options.from_job(service.job_settings(self.db, job_id))
            from .ocr.registry import primary_engine, secondary_engine
            primary = primary_engine()
            secondary, sec_err = None, None
            try:
                secondary = secondary_engine()
                if secondary is None:
                    sec_err = "second OCR engine disabled in configuration"
            except EngineUnavailable as exc:
                sec_err = str(exc)
            result = pipeline.analyze_loaded(loaded, opts, primary, secondary, sec_err,
                                             should_stop=self._stop.is_set)
            self._save(image_id, job_id, result, time.time() - t0)
        except imaging.ImageLoadError as exc:
            self._fail(image_id, job_id, f"unreadable image: {exc}", final=True)
        except InterruptedError:
            with self.db.tx() as c:
                c.execute("UPDATE images SET proc_status='queued' WHERE id=?", (image_id,))
        except FileNotFoundError as exc:
            self._fail(image_id, job_id, f"stored file missing: {exc}", final=True)
        except Exception as exc:
            tb = traceback.format_exc(limit=4)
            log.error("image %s failed: %s\n%s", image_id, exc, tb)
            final = row["attempts"] >= settings.max_attempts
            self._fail(image_id, job_id, f"{type(exc).__name__}: {exc}", final=final)

    def _fail(self, image_id: int, job_id: int, msg: str, final: bool) -> None:
        with self.db.tx() as c:
            if final:
                c.execute("UPDATE images SET proc_status='error', auto_result=NULL, error=?, finished_at=?, "
                          "reasons_json=? WHERE id=?",
                          (msg[:2000], time.time(), json.dumps([f"processing failed: {msg[:300]}"]), image_id))
                self.db.log("failed", msg[:2000], job_id, image_id, conn=c)
            else:
                c.execute("UPDATE images SET proc_status='queued', error=? WHERE id=?", (msg[:2000], image_id))
                self.db.log("retry", msg[:2000], job_id, image_id, conn=c)

    def _save(self, image_id: int, job_id: int, a: pipeline.Analysis, elapsed: float) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM hits WHERE image_id=?", (image_id,))
            c.execute("DELETE FROM passes WHERE image_id=?", (image_id,))
            pass_ids: dict[int, int] = {}
            for p in a.passes:
                cur = c.execute(
                    "INSERT INTO passes(image_id, seq, stage, engine, variant, duration_ms, status, error,"
                    " n_lines, mean_conf, text, lines_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (image_id, p.seq, p.stage, p.engine, p.variant, p.duration_ms, p.status, p.error,
                     len(p.lines), p.mean_conf, p.text, json.dumps(p.lines)))
                pass_ids[p.seq] = cur.lastrowid
            for h in a.hits:
                c.execute(
                    "INSERT INTO hits(image_id, pass_id, kind, target, raw, normalized, confidence, poly_json,"
                    " box_approx, line_text, engine, variant, occurrence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (image_id, pass_ids.get(h.pass_seq), h.kind, h.target, h.raw, h.normalized, h.confidence,
                     json.dumps(h.poly), int(h.approx), h.line_text, h.engine, h.variant, h.occurrence))
            c.execute(
                "UPDATE images SET proc_status='done', auto_result=?, reasons_json=?, warnings_json=?, error=NULL,"
                " finished_at=?, best_confidence=?, occurrences=?, words_found=?, word_counts_json=?, width=?,"
                " height=?, format=?, sharpness=? WHERE id=?",
                (a.status, json.dumps(a.reasons), json.dumps(a.warnings), time.time(), a.best_confidence,
                 a.occurrences, ";".join(a.words_found), json.dumps(a.word_counts), a.width, a.height,
                 a.format, a.sharpness, image_id))
            self.db.log("processed", {"result": a.status, "passes": len(a.passes), "seconds": round(elapsed, 2)},
                        job_id, image_id, conn=c)
