"""
storage.py — Persistence layer for Provenance Guard.

Two tables:
  - content:   the current, mutable state of each submission (used by /appeal
               to look up a content_id and flip its status).
  - audit_log: an append-only, structured record of every decision and appeal.
               This is the canonical audit trail surfaced by GET /log.

SQLite is built into Python, so there is nothing to install. We open a fresh
connection per call (the Flask dev server is threaded) and store rich detail
as JSON in the audit_log so the schema stays stable as the pipeline grows.
"""

import json
import sqlite3
import datetime
from pathlib import Path

DB_PATH = Path(__file__).with_name("provenance.db")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they do not already exist. Safe to call repeatedly."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS content (
                content_id   TEXT PRIMARY KEY,
                creator_id   TEXT NOT NULL,
                text         TEXT NOT NULL,
                attribution  TEXT NOT NULL,
                ai_score     REAL NOT NULL,
                confidence   REAL NOT NULL,
                llm_score    REAL NOT NULL,
                stylo_score  REAL NOT NULL,
                status       TEXT NOT NULL,
                created_at   TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                content_id  TEXT NOT NULL,
                creator_id  TEXT NOT NULL,
                timestamp   TEXT NOT NULL,
                event       TEXT NOT NULL,       -- 'classified' | 'appeal'
                attribution TEXT,
                confidence  REAL,
                ai_score    REAL,
                llm_score   REAL,
                stylo_score REAL,
                status      TEXT NOT NULL,
                details     TEXT                  -- JSON: signals breakdown, appeal text, etc.
            )
            """
        )


def now_iso():
    """UTC timestamp in ISO-8601 with a trailing Z, e.g. 2026-06-29T14:32:10.123Z."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def record_classification(record):
    """Persist a new submission's current state and write its audit entry.

    `record` is the dict assembled by the /submit handler.
    """
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO content (content_id, creator_id, text, attribution,
                                 ai_score, confidence, llm_score, stylo_score,
                                 status, created_at)
            VALUES (:content_id, :creator_id, :text, :attribution, :ai_score,
                    :confidence, :llm_score, :stylo_score, :status, :timestamp)
            """,
            record,
        )
        conn.execute(
            """
            INSERT INTO audit_log (content_id, creator_id, timestamp, event,
                                   attribution, confidence, ai_score, llm_score,
                                   stylo_score, status, details)
            VALUES (:content_id, :creator_id, :timestamp, 'classified',
                    :attribution, :confidence, :ai_score, :llm_score,
                    :stylo_score, :status, :details)
            """,
            {**record, "details": json.dumps(record.get("signals", {}))},
        )


def get_content(content_id):
    """Return the current row for a content_id, or None if it doesn't exist."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM content WHERE content_id = ?", (content_id,)
        ).fetchone()
        return dict(row) if row else None


def record_appeal(content_id, creator_reasoning):
    """Flip a submission to 'under_review' and log the appeal next to its
    original decision. Returns the updated content row, or None if not found.
    """
    content = get_content(content_id)
    if content is None:
        return None

    timestamp = now_iso()
    with _connect() as conn:
        conn.execute(
            "UPDATE content SET status = 'under_review' WHERE content_id = ?",
            (content_id,),
        )
        conn.execute(
            """
            INSERT INTO audit_log (content_id, creator_id, timestamp, event,
                                   attribution, confidence, ai_score, llm_score,
                                   stylo_score, status, details)
            VALUES (?, ?, ?, 'appeal', ?, ?, ?, ?, ?, 'under_review', ?)
            """,
            (
                content_id,
                content["creator_id"],
                timestamp,
                content["attribution"],
                content["confidence"],
                content["ai_score"],
                content["llm_score"],
                content["stylo_score"],
                json.dumps({"appeal_reasoning": creator_reasoning}),
            ),
        )
    content["status"] = "under_review"
    content["appealed_at"] = timestamp
    return content


def get_log(limit=50):
    """Return the most recent audit entries as a list of plain dicts, newest
    first, with the JSON `details` column expanded back into nested fields.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    entries = []
    for row in rows:
        entry = dict(row)
        details = json.loads(entry.pop("details") or "{}")
        # Surface the most useful detail fields at the top level for readability.
        if "appeal_reasoning" in details:
            entry["appeal_reasoning"] = details["appeal_reasoning"]
        if details and "appeal_reasoning" not in details:
            entry["signals"] = details
        entries.append(entry)
    return entries
