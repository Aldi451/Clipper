"""Lapisan SQLite sederhana untuk menyimpan project (job) dan clip."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from .config import DB_FILE, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL DEFAULT 'upload',
    status        TEXT NOT NULL DEFAULT 'queued',
    stage         TEXT NOT NULL DEFAULT '',
    progress      REAL NOT NULL DEFAULT 0,
    message       TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT '',
    source_path   TEXT NOT NULL DEFAULT '',
    duration      REAL NOT NULL DEFAULT 0,
    width         INTEGER NOT NULL DEFAULT 0,
    height        INTEGER NOT NULL DEFAULT 0,
    error         TEXT NOT NULL DEFAULT '',
    meta          TEXT NOT NULL DEFAULT '{}',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS clips (
    id            TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL,
    idx           INTEGER NOT NULL DEFAULT 0,
    start         REAL NOT NULL DEFAULT 0,
    duration      REAL NOT NULL DEFAULT 0,
    score         REAL NOT NULL DEFAULT 0,
    title         TEXT NOT NULL DEFAULT '',
    hook          TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    hashtags      TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'suggested',
    progress      REAL NOT NULL DEFAULT 0,
    message       TEXT NOT NULL DEFAULT '',
    file          TEXT NOT NULL DEFAULT '',
    thumb         TEXT NOT NULL DEFAULT '',
    yt_id         TEXT NOT NULL DEFAULT '',
    yt_url        TEXT NOT NULL DEFAULT '',
    yt_privacy    TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    meta          TEXT NOT NULL DEFAULT '{}',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_clips_job ON clips(job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
"""

JOB_FIELDS = {
    "kind", "status", "stage", "progress", "message", "title", "source", "source_path",
    "duration", "width", "height", "error", "meta", "updated_at",
}
CLIP_FIELDS = {
    "idx", "start", "duration", "score", "title", "hook", "description", "hashtags",
    "status", "progress", "message", "file", "thumb", "yt_id", "yt_url", "yt_privacy",
    "error", "meta", "updated_at",
}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def _dump(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for key in ("meta",):
        if key in data and isinstance(data[key], str):
            try:
                data[key] = json.loads(data[key])
            except Exception:
                data[key] = {}
    return data


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #
def create_job(kind: str, **fields: Any) -> dict[str, Any]:
    job_id = new_id("job")
    now = time.time()
    values = {
        "id": job_id,
        "kind": kind,
        "status": "queued",
        "stage": "antre",
        "progress": 0.0,
        "message": "",
        "title": fields.get("title", ""),
        "source": fields.get("source", ""),
        "source_path": fields.get("source_path", ""),
        "duration": float(fields.get("duration") or 0),
        "width": int(fields.get("width") or 0),
        "height": int(fields.get("height") or 0),
        "meta": json.dumps(fields.get("meta") or {}, ensure_ascii=False),
        "created_at": now,
        "updated_at": now,
    }
    with connect() as conn:
        conn.execute(
            f"INSERT INTO jobs ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
            list(values.values()),
        )
    return get_job(job_id)  # type: ignore[return-value]


def update_job(job_id: str, **fields: Any) -> None:
    patch = {k: _dump(v) for k, v in fields.items() if k in JOB_FIELDS}
    if not patch:
        return
    patch["updated_at"] = time.time()
    assignments = ", ".join(f"{key} = ?" for key in patch)
    with connect() as conn:
        conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", [*patch.values(), job_id])


def get_job(job_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_dict(row)


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_dict(r) for r in rows]  # type: ignore[misc]


def delete_job(job_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM clips WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


# --------------------------------------------------------------------------- #
# clips
# --------------------------------------------------------------------------- #
def create_clip(job_id: str, **fields: Any) -> dict[str, Any]:
    clip_id = new_id("clip")
    now = time.time()
    values = {
        "id": clip_id,
        "job_id": job_id,
        "idx": int(fields.get("idx") or 0),
        "start": float(fields.get("start") or 0),
        "duration": float(fields.get("duration") or 0),
        "score": float(fields.get("score") or 0),
        "title": fields.get("title") or "",
        "hook": fields.get("hook") or "",
        "description": fields.get("description") or "",
        "hashtags": fields.get("hashtags") or "",
        "status": fields.get("status") or "suggested",
        "progress": 0.0,
        "message": "",
        "file": "",
        "thumb": "",
        "meta": json.dumps(fields.get("meta") or {}, ensure_ascii=False),
        "created_at": now,
        "updated_at": now,
    }
    with connect() as conn:
        conn.execute(
            f"INSERT INTO clips ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
            list(values.values()),
        )
    return get_clip(clip_id)  # type: ignore[return-value]


def update_clip(clip_id: str, **fields: Any) -> None:
    patch = {k: _dump(v) for k, v in fields.items() if k in CLIP_FIELDS}
    if not patch:
        return
    patch["updated_at"] = time.time()
    assignments = ", ".join(f"{key} = ?" for key in patch)
    with connect() as conn:
        conn.execute(f"UPDATE clips SET {assignments} WHERE id = ?", [*patch.values(), clip_id])


def get_clip(clip_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    return _row_to_dict(row)


def list_clips(job_id: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if job_id:
            rows = conn.execute(
                "SELECT * FROM clips WHERE job_id = ? ORDER BY idx ASC", (job_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM clips ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
    return [_row_to_dict(r) for r in rows]  # type: ignore[misc]


def delete_clip(clip_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))


def reset_jobs_on_startup() -> None:
    """Job yang masih 'running' saat server mati ditandai error supaya UI tidak bingung."""
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET status='error', error='Server berhenti saat proses berjalan', "
            "stage='gagal', updated_at=? WHERE status IN ('running','queued')",
            (time.time(),),
        )
        conn.execute(
            "UPDATE clips SET status='suggested', progress=0, message='' "
            "WHERE status='rendering'"
        )
