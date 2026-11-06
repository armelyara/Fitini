"""
db.py — SQLite persistence for Fitini Voice Analyzer.

Stores analysis sessions (archive + per-session report), target dossiers, and the live
settings. Only settings are seeded; sessions and targets start empty and fill as real
analyses run.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

DB_PATH = os.environ.get("FITINI_DB", os.path.join(os.environ.get("FITINI_DATA", os.getcwd()), "fitini.db"))
_LOCK = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(config: Dict[str, Any]) -> None:
    with _LOCK, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, subject TEXT, channel TEXT, date TEXT,
                dur TEXT, status TEXT, status_color TEXT, created_at REAL, report_json TEXT
            );
            CREATE TABLE IF NOT EXISTS targets (code TEXT PRIMARY KEY, data_json TEXT);
            CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK (id = 1), data_json TEXT);
            """
        )
        # Settings are the only seeded row. Sessions/targets start EMPTY and fill as
        # real analyses run -- no demo content.
        if conn.execute("SELECT COUNT(*) AS n FROM settings").fetchone()["n"] == 0:
            conn.execute("INSERT OR REPLACE INTO settings VALUES (1, ?)",
                         (json.dumps(config.get("default_settings", {})),))


def list_sessions(query: Optional[str] = None) -> List[Dict[str, Any]]:
    with _LOCK, _connect() as conn:
        rows = conn.execute("SELECT * FROM sessions ORDER BY created_at DESC").fetchall()
    out = []
    for r in rows:
        if query:
            q = query.lower()
            if q not in r["subject"].lower() and q not in r["id"].lower():
                continue
        out.append({
            "id": r["id"], "subject": r["subject"], "channel": r["channel"],
            "date": r["date"], "dur": r["dur"], "status": r["status"],
            "statusColor": r["status_color"],
        })
    return out


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK, _connect() as conn:
        r = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not r:
        return None
    report = json.loads(r["report_json"] or "{}")
    return {
        "id": r["id"], "subject": r["subject"], "channel": r["channel"],
        "date": r["date"], "dur": r["dur"], "status": r["status"],
        "statusColor": r["status_color"], "report": report,
    }


def create_session(session: Dict[str, Any]) -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
            (
                session["id"], session.get("subject", "UNASSIGNED"),
                session.get("channel", "VOICE · LIVE"), session.get("date", ""),
                session.get("dur", "—"), session.get("status", "REVIEW"),
                session.get("statusColor", "#d8b24a"), time.time(),
                json.dumps(session.get("report", {})),
            ),
        )


def get_target(code: str) -> Optional[Dict[str, Any]]:
    with _LOCK, _connect() as conn:
        r = conn.execute("SELECT data_json FROM targets WHERE code = ?", (code,)).fetchone()
    return json.loads(r["data_json"]) if r else None


def get_settings() -> Dict[str, Any]:
    with _LOCK, _connect() as conn:
        r = conn.execute("SELECT data_json FROM settings WHERE id = 1").fetchone()
    return json.loads(r["data_json"]) if r else {}


def save_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK, _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES (1, ?)", (json.dumps(settings),))
    return settings
