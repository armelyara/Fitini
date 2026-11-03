"""
server.py — FastAPI backend for Fitini (Path B: canonical fitini_core output).

The investigation engine is the unified `fitini_core.CorrelationEngine`, run over the
knowledge packs the operator selects in the UI (resolved by `packs.load_domains`). The
WebSocket streams the engine's canonical `AnalysisResult` per utterance; the front
consumes it directly (no legacy frame, no adapter).

Endpoints
---------
GET  /                           -> static/index.html (the vanilla front)
POST /api/login                  -> { token }
POST /api/logout
GET  /api/me                     -> operator + settings            (auth)
GET  /api/domains                -> selectable investigation domains
GET  /api/sessions[?query=]      -> archive list                   (auth)
GET  /api/sessions/{id}          -> session report                 (auth)
GET  /api/targets/{code}         -> target dossier                 (auth)
GET  /api/settings | PUT         -> settings                       (auth)
POST /api/analyze                -> upload + transcribe + analyze    (auth)
WS   /ws/intercept?token=&domains=justice,cti -> live canonical stream

Simulation mode is the default: with only fastapi + uvicorn installed the whole app
runs, replaying the scripted stream. Set USE_MIC=1 for live microphone capture
(optional ML extras). All model inference runs off the event loop.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

from fastapi import (Depends, FastAPI, File, Header, HTTPException, Query,
                     UploadFile, WebSocket, WebSocketDisconnect)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import packs
from audio_transcriber import AudioStreamTranscriber
from auth import AuthManager
from fitini_core import CorrelationEngine

BASE_DIR = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATIC_DIR = os.path.join(BASE_DIR, "static")

with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
    CONFIG: Dict[str, Any] = json.load(fh)

db.init_db(CONFIG)
auth_manager = AuthManager(CONFIG)

app = FastAPI(title="Fitini")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ----------------------------- auth helpers -------------------------------- #
def _token_from_header(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def require_auth(authorization: Optional[str] = Header(None)) -> str:
    token = _token_from_header(authorization)
    if not auth_manager.validate(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return token


class LoginBody(BaseModel):
    operator_id: str
    passphrase: str


class SettingsBody(BaseModel):
    settings: Dict[str, Any]


# ------------------------------- routes ------------------------------------ #
@app.get("/")
async def serve_ui():
    index = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    return JSONResponse({"detail": "static/index.html not found"}, status_code=404)


@app.post("/api/login")
async def login(body: LoginBody):
    token = auth_manager.login(body.operator_id, body.passphrase)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication failed")
    return {"token": token, "operator_id": auth_manager.operator_id}


@app.post("/api/logout")
async def logout(authorization: Optional[str] = Header(None)):
    auth_manager.logout(_token_from_header(authorization))
    return {"ok": True}


@app.get("/api/me")
async def me(_: str = Depends(require_auth)):
    return {"operator_id": auth_manager.operator_id, "settings": db.get_settings()}


@app.get("/api/domains")
async def domains():
    """The selectable investigation domains (mandatory multi-select in the UI)."""
    return {"domains": packs.domain_catalog()}


@app.get("/api/sessions")
async def sessions(query: Optional[str] = Query(None), _: str = Depends(require_auth)):
    return {"sessions": db.list_sessions(query)}


@app.get("/api/sessions/{session_id}")
async def session_detail(session_id: str, _: str = Depends(require_auth)):
    s = db.get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    return s


@app.get("/api/targets/{code}")
async def target(code: str, _: str = Depends(require_auth)):
    t = db.get_target(code.upper())
    if not t:
        raise HTTPException(status_code=404, detail="Target not found")
    return t


@app.get("/api/settings")
async def read_settings(_: str = Depends(require_auth)):
    return {"settings": db.get_settings()}


@app.put("/api/settings")
async def update_settings(body: SettingsBody, _: str = Depends(require_auth)):
    merged = {**db.get_settings(), **body.settings}
    return {"settings": db.save_settings(merged)}


# ---------------------------- engine helpers ------------------------------- #
def _parse_domains(domains_param: Optional[str]) -> List[str]:
    ids = [d.strip() for d in (domains_param or "").split(",") if d.strip()]
    return ids or ["justice"]  # the UI always sends a selection; fall back defensively


def _engine_for(domains_param: Optional[str]) -> CorrelationEngine:
    ids = _parse_domains(domains_param)
    try:
        profile = packs.load_domains(ids)
    except Exception:
        profile = packs.load_domains(["justice"])
    return CorrelationEngine(profile)


def _canonical_report(engine: CorrelationEngine, alerts: List[Dict[str, Any]],
                      duration: str) -> Dict[str, Any]:
    """Build a session report straight from the engine's canonical state."""
    return {
        "summary": {
            "locked": engine.locked,
            "leading": engine.leading,
            "entities": list(engine.entities.keys()),
        },
        "entities": [{"name": k, "category": v["category"], "count": v["count"]}
                     for k, v in engine.entities.items()],
        "relationships": [list(r) for r in engine.relationships],
        "timeline": engine.timeline.export(),
        "alerts": alerts,
        "duration": duration,
        "lines": engine.line,
    }


def _flag_canonicals(p):
    """Words that fired a rule THIS frame: an entity (or a spoken word linked to one)
    that triggered an alert, or that newly satisfied a hypothesis criterion. The UI
    draws a flag arrow only for these -- engine logic, not decoration."""
    segs = p.get("segs", [])
    shown = [s["canonical"] for s in segs if s.get("canonical")]
    newset = set(p.get("new_entities", []))
    interest = set()
    for a in p.get("alerts", []):
        val = a.get("value")
        if val:
            for s in segs:
                if s.get("canonical") and s.get("text") == val:
                    interest.add(s["canonical"])
        for e in (a.get("all_of") or []):
            interest.add(e)
    for h in p.get("hypotheses", []):
        for e in (h.get("present") or []):
            if e in newset:
                interest.add(e)
    linked = set()
    for pair in p.get("relationships", []):
        if len(pair) >= 2:
            a, b = pair[0], pair[1]
            if a in interest:
                linked.add(b)
            if b in interest:
                linked.add(a)
    out = [c for c in shown if c in interest or c in linked]
    return list(dict.fromkeys(out))


def _persist_session(engine: CorrelationEngine, report: Dict[str, Any],
                     channel: str, dur: str) -> str:
    locked = engine.locked
    ents = list(engine.entities.keys())
    subject = locked or (ents[0] if ents else "UNASSIGNED")
    session_id = f"CH07-{int(time.time()) % 10000:04d}"
    db.create_session({
        "id": session_id,
        "subject": subject,
        "channel": channel,
        "date": time.strftime("%d %b %y").upper(),
        "dur": dur,
        "status": "LOCKED" if locked else "REVIEW",
        "statusColor": "#38ef7d" if locked else "#d8b24a",
        "report": report,
    })
    return session_id


# ------------------------------- analyze ----------------------------------- #
@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...),
                  domains: Optional[str] = Query(None),
                  _: str = Depends(require_auth)):
    engine = _engine_for(domains)
    transcriber = AudioStreamTranscriber(CONFIG, use_microphone=False)

    suffix = os.path.splitext(file.filename or "")[1] or ".wav"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    alerts: List[Dict[str, Any]] = []
    try:
        tmp.write(await file.read())
        tmp.close()
        count = 0
        try:
            async for item in transcriber.transcribe_file(tmp.name):
                r = engine.analyze_utterance(item["text"], item.get("ts"))
                alerts += r.get("alerts", [])
                count += 1
        except Exception:
            # No Whisper / unsupported file here: fall back to the scripted stream.
            for line in CONFIG.get("simulated_intercept", []):
                r = engine.analyze_utterance(line["text"], line["ts"])
                alerts += r.get("alerts", [])
                count += 1
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    report = _canonical_report(engine, alerts, duration="—")
    session_id = _persist_session(engine, report, channel="FILE · UPLOAD", dur="—")
    return {"session_id": session_id, "report": report, "lines": report["lines"],
            "filename": file.filename}


# ------------------------------ websocket ---------------------------------- #
@app.websocket("/ws/intercept")
async def websocket_intercept(websocket: WebSocket,
                              token: Optional[str] = Query(None),
                              domains: Optional[str] = Query(None)):
    if not auth_manager.validate(token):
        await websocket.close(code=4401)
        return
    await websocket.accept()

    engine = _engine_for(domains)
    use_mic = os.getenv("USE_MIC", "0") == "1"
    transcriber = AudioStreamTranscriber(CONFIG, use_microphone=use_mic)
    alerts: List[Dict[str, Any]] = []

    try:
        async for utt in transcriber.stream_transcriptions():
            payload = engine.analyze_utterance(utt["text"], utt.get("ts"))
            payload["type"] = "utterance"
            payload["flag_canonicals"] = _flag_canonicals(payload)
            alerts += payload.get("alerts", [])
            await websocket.send_json(payload)

        report = _canonical_report(engine, alerts, duration="00:59")
        session_id = _persist_session(engine, report, channel="VOICE · LIVE", dur="00:59")
        await websocket.send_json({"type": "complete", "session_id": session_id,
                                   "report": report})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover
        try:
            await websocket.send_json({"type": "error", "detail": str(exc)})
        except Exception:
            pass
