"""
db.py — SQLite persistence for Fitini Voice Analyzer.

Stores intercept sessions (archive + per-session report), target dossiers, and the live
settings. Seeded on first run from the data that used to be hardcoded in the UI, so the
Archive / Report / Dossier screens are populated out of the box. New live-intercept and
file-analysis sessions are appended here.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

DB_PATH = os.environ.get("FITINI_DB", os.path.join(os.path.dirname(__file__), "fitini.db"))
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
                id TEXT PRIMARY KEY,
                subject TEXT,
                channel TEXT,
                date TEXT,
                dur TEXT,
                status TEXT,
                status_color TEXT,
                created_at REAL,
                report_json TEXT
            );
            CREATE TABLE IF NOT EXISTS targets (
                code TEXT PRIMARY KEY,
                data_json TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                data_json TEXT
            );
            """
        )
        cur = conn.execute("SELECT COUNT(*) AS n FROM sessions")
        if cur.fetchone()["n"] == 0:
            _seed(conn, config)


# ---------------------------------------------------------------------------- #
def _seed(conn: sqlite3.Connection, config: Dict[str, Any]) -> None:
    C = {"green": "#38ef7d", "sage": "#78a890", "muted": "#78827f",
         "alert": "#e2574f", "scan": "#d8b24a"}

    full_report = {
        "stats": [
            {"label": "DURATION", "val": "00:59", "color": "#b8c4c2"},
            {"label": "ENTITIES", "val": "06", "color": C["green"]},
            {"label": "TARGETS LOCKED", "val": "02", "color": C["green"]},
            {"label": "THREAT LEVEL", "val": "HIGH", "color": C["alert"]},
        ],
        "entities": [
            {"name": "AMIRI", "cls": "SUBJECT", "conf": "0.94", "cColor": C["green"]},
            {"name": "ALEXANDER", "cls": "ALIAS → AMIRI", "conf": "0.91", "cColor": C["green"]},
            {"name": "FLYING FOWL", "cls": "ASSET", "conf": "0.88", "cColor": C["scan"]},
            {"name": "CAT BURGLAR", "cls": "ACTOR", "conf": "0.79", "cColor": C["scan"]},
            {"name": "UKRAINE", "cls": "PLACE", "conf": "0.85", "cColor": C["green"]},
            {"name": "72 HOURS", "cls": "DEADLINE", "conf": "0.90", "cColor": C["green"]},
        ],
        "targets": [
            {"code": "AMIRI", "detail": "Subject · ID confirmed · alias ALEXANDER"},
            {"code": "FLYING FOWL", "detail": "Asset movement · 72h window"},
        ],
        "timeline": [
            {"t": "09:11", "e": "Subject ALEXANDER referenced"},
            {"t": "09:11", "e": "CAT BURGLAR intrusion reported"},
            {"t": "09:11", "e": "Alias resolved → AMIRI, ID LOCKED"},
            {"t": "09:11", "e": "FLYING FOWL movement · 72 HOURS"},
        ],
    }

    sessions = [
        ("CH07-0912", "AMIRI", "VOICE · SAT", "31 OCT 26", "00:59", "LOCKED", C["green"], full_report),
        ("CH04-0877", "CAT BURGLAR", "VOICE · GSM", "28 OCT 26", "04:12", "REVIEW", C["scan"], full_report),
        ("CH11-0841", "UNASSIGNED", "TEXT · SMS", "24 OCT 26", "—", "CLOSED", C["muted"], full_report),
        ("CH02-0802", "BLACK SEA", "VOICE · VOIP", "19 OCT 26", "12:40", "ALERT", C["alert"], full_report),
        ("CH09-0790", "THE ARCHITECT", "VOICE · SAT", "15 OCT 26", "02:03", "REVIEW", C["scan"], full_report),
        ("CH05-0761", "UKRAINE NODE", "TEXT · APP", "11 OCT 26", "—", "CLOSED", C["muted"], full_report),
    ]
    now = time.time()
    for i, s in enumerate(sessions):
        conn.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
            (s[0], s[1], s[2], s[3], s[4], s[5], s[6], now - i * 86400, json.dumps(s[7])),
        )

    amiri = {
        "code": "AMIRI",
        "alias": "ALEXANDER",
        "threat": "HIGH",
        "confidence": "0.94",
        "last_seen": "CH.07 · 09:12",
        "subtitle": "CANONICAL SUBJECT · ID CONFIRMED",
        "aliases": [
            {"from": "ALEXANDER", "to": "AMIRI", "status": "confirmed"},
            {"from": "THE ARCHITECT", "to": None, "status": "unconfirmed"},
        ],
        "locations": ["UKRAINE", "BLACK SEA"],
        "network": [
            {"name": "AMIRI", "kind": "subject"},
            {"name": "ALEXANDER", "kind": "alias"},
            {"name": "UKRAINE", "kind": "place"},
            {"name": "BLACK SEA", "kind": "place"},
            {"name": "FLYING FOWL", "kind": "asset"},
            {"name": "CAT BURGLAR", "kind": "threat"},
        ],
        "related": [
            {"id": "CH04-0877", "note": "CAT BURGLAR intrusion", "date": "28 OCT"},
            {"id": "CH02-0802", "note": "BLACK SEA logistics", "date": "19 OCT"},
            {"id": "CH09-0790", "note": "Alias THE ARCHITECT", "date": "15 OCT"},
        ],
    }
    conn.execute("INSERT INTO targets VALUES (?,?)", ("AMIRI", json.dumps(amiri)))
    conn.execute(
        "INSERT OR REPLACE INTO settings VALUES (1, ?)",
        (json.dumps(config.get("default_settings", {})),),
    )


# ---------------------------------------------------------------------------- #
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
