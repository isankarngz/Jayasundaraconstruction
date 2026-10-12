"""SQLite persistence.  Every state change is committed immediately so a
browser refresh, server restart or crash never loses completed work."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    created_at    REAL NOT NULL,
    state         TEXT NOT NULL DEFAULT 'running',      -- running | paused
    settings_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS images (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id         INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    sha256         TEXT NOT NULL,
    original_name  TEXT NOT NULL,
    rel_path       TEXT NOT NULL,
    ext            TEXT NOT NULL,
    format         TEXT,
    size_bytes     INTEGER NOT NULL,
    width          INTEGER,
    height         INTEGER,
    uploaded_at    REAL NOT NULL,
    stored_name    TEXT NOT NULL,
    duplicate_of   INTEGER REFERENCES images(id),
    -- queued | processing | done | error | duplicate
    proc_status    TEXT NOT NULL DEFAULT 'queued',
    -- match | no_match | review   (automated outcome, NULL until done)
    auto_result    TEXT,
    reasons_json   TEXT NOT NULL DEFAULT '[]',
    warnings_json  TEXT NOT NULL DEFAULT '[]',
    error          TEXT,
    attempts       INTEGER NOT NULL DEFAULT 0,
    started_at     REAL,
    finished_at    REAL,
    best_confidence REAL,
    occurrences    INTEGER NOT NULL DEFAULT 0,
    words_found    TEXT NOT NULL DEFAULT '',
    word_counts_json TEXT NOT NULL DEFAULT '{}',
    sharpness      REAL,
    -- human verification: match | no_match | unreadable | NULL
    review_decision TEXT,
    review_method   TEXT,
    review_note     TEXT,
    reviewed_at     REAL
);
CREATE INDEX IF NOT EXISTS ix_images_job ON images(job_id, proc_status);
CREATE INDEX IF NOT EXISTS ix_images_hash ON images(job_id, sha256);

CREATE TABLE IF NOT EXISTS passes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id    INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    stage       TEXT NOT NULL,
    engine      TEXT NOT NULL,
    variant     TEXT NOT NULL,
    duration_ms INTEGER,
    status      TEXT NOT NULL,          -- ok | error
    error       TEXT,
    n_lines     INTEGER NOT NULL DEFAULT 0,
    mean_conf   REAL,
    text        TEXT NOT NULL DEFAULT '',
    lines_json  TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ix_passes_image ON passes(image_id);

CREATE TABLE IF NOT EXISTS hits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id    INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    pass_id     INTEGER REFERENCES passes(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    target      TEXT NOT NULL,
    raw         TEXT NOT NULL,
    normalized  TEXT NOT NULL,
    confidence  REAL,
    poly_json   TEXT NOT NULL,
    box_approx  INTEGER NOT NULL DEFAULT 0,
    line_text   TEXT NOT NULL,
    engine      TEXT NOT NULL,
    variant     TEXT NOT NULL,
    occurrence  INTEGER                -- cluster id after cross-pass de-duplication
);
CREATE INDEX IF NOT EXISTS ix_hits_image ON hits(image_id);

CREATE TABLE IF NOT EXISTS rejected (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    original_name TEXT NOT NULL,
    reason        TEXT NOT NULL,
    at            REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id    INTEGER,
    image_id  INTEGER,
    at        REAL NOT NULL,
    kind      TEXT NOT NULL,
    detail    TEXT
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._local = threading.local()
        self.write_lock = threading.RLock()
        with self.connect() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=60, isolation_level=None,
                                   check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        yield self._conn()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Serialised write transaction."""
        with self.write_lock:
            conn = self._conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def query(self, sql: str, args: tuple | list = ()) -> list[sqlite3.Row]:
        return self._conn().execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple | list = ()) -> sqlite3.Row | None:
        return self._conn().execute(sql, args).fetchone()

    def log(self, kind: str, detail: Any = None, job_id: int | None = None,
            image_id: int | None = None, conn: sqlite3.Connection | None = None) -> None:
        payload = detail if isinstance(detail, str) or detail is None else json.dumps(detail)
        sql = "INSERT INTO events(job_id, image_id, at, kind, detail) VALUES (?,?,?,?,?)"
        if conn is not None:
            conn.execute(sql, (job_id, image_id, time.time(), kind, payload))
        else:
            with self.tx() as c:
                c.execute(sql, (job_id, image_id, time.time(), kind, payload))
