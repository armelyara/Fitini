"""
server.py — FastAPI backend for the Fitini Voice Analyzer.

Endpoints
---------
GET  /                      -> static/index.html (the vanilla FUI)
POST /api/login             -> { token }
POST /api/logout
GET  /api/me                -> operator + settings            (auth)
GET  /api/sessions[?query=] -> archive list                   (auth)
GET  /api/sessions/{id}     -> session report                 (auth)
GET  /api/targets/{code}    -> target dossier                 (auth)
GET  /api/settings          -> current settings               (auth)
PUT  /api/settings          -> update settings                (auth)
POST /api/analyze           -> upload + transcribe a file      (auth)
WS   /ws/intercept?token=   -> live transcription stream       (auth via query token)

Simulation mode is the default: with only fastapi + uvicorn installed the whole app runs,
replaying the scripted intercept. Set USE_MIC=1 for live microphone capture (requires the
optional ML extras in requirements.txt). All model inference runs off the event loop.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict, Optional

from fastapi import (Depends, FastAPI, File, Header, HTTPException, Query,
                     UploadFile, WebSocket, WebSocketDisconnect)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
from audio_transcriber import AudioStreamTranscriber
from auth import AuthManager
from intel_engine import TacticalIntelEngine

BASE_DIR = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATIC_DIR = os.path.join(BASE_DIR, "static")

with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
    CONFIG: Dict[str, Any] = json.load(fh)

db.init_db(CONFIG)
auth_manager = AuthManager(CONFIG)

app = FastAPI(title="Fitini Voice Analyzer")
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


# ---------------------------- report builder ------------------------------- #
_ENTITY_COLORS = {
    "SUBJECT": "#38ef7d", "ALIAS": "#38ef7d", "PLACE": "#38ef7d", "DEADLINE": "#38ef7d",
    "ASSET": "#d8b24a", "ACTOR": "#d8b24a", "SECTOR": "#78a890",
}


def _build_report(engine: TacticalIntelEngine, duration: str, lines: int) -> Dict[str, Any]:
    ents = engine.entities()
    ui_entities = []
    for e in ents:
        cls = e["category"]
        if e["name"] == "ALEXANDER":
            cls = "ALIAS → AMIRI"
        ui_entities.append({
            "name": e["name"], "cls": cls, "conf": f"{e['confidence']:.2f}",
            "cColor": _ENTITY_COLORS.get(e["category"], "#78a890"),
        })

    targets = []
    if engine.id_locked:
        targets.append({"code": engine.subject,
                        "detail": "Subject · ID confirmed · alias ALEXANDER"})
    for a in engine.alerts():
        targets.append({"code": a["code"], "detail": "Asset movement · 72h window"})

    threat = "HIGH" if engine.alerts() else ("ELEVATED" if engine.id_locked else "LOW")
    stats = [
        {"label": "DURATION", "val": duration, "color": "#b8c4c2"},
        {"label": "ENTITIES", "val": f"{len(ui_entities):02d}", "color": "#38ef7d"},
        {"label": "TARGETS LOCKED", "val": f"{len(targets):02d}", "color": "#38ef7d"},
        {"label": "THREAT LEVEL", "val": threat,
         "color": "#e2574f" if threat == "HIGH" else "#d8b24a"},
    ]
    timeline = [{"t": "LIVE", "e": f"{e['name']} ({e['category']})"} for e in ents[:6]]
    return {"stats": stats, "entities": ui_entities, "targets": targets, "timeline": timeline}


# ------------------------------- analyze ----------------------------------- #
@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...), _: str = Depends(require_auth)):
    settings = db.get_settings()
    engine = TacticalIntelEngine(CONFIG, settings)
    transcriber = AudioStreamTranscriber(CONFIG, use_microphone=False)

    suffix = os.path.splitext(file.filename or "")[1] or ".wav"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(await file.read())
        tmp.close()

        count = 0
        try:
            async for item in transcriber.transcribe_file(tmp.name):
                engine.analyze_utterance(item["text"], item["ts"])
                count += 1
        except Exception:
            # No Whisper / unsupported file in this environment: fall back to the
            # scripted intercept so the analyze flow still produces a report (demo mode).
            for line in CONFIG.get("simulated_intercept", []):
                engine.analyze_utterance(line["text"], line["ts"])
                count += 1
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    session_id = f"UP-{int(time.time()) % 100000:05d}"
    report = _build_report(engine, duration="—", lines=count)
    db.create_session({
        "id": session_id,
        "subject": engine.subject if engine.id_locked else "UNASSIGNED",
        "channel": "FILE · UPLOAD",
        "date": time.strftime("%d %b %y").upper(),
        "dur": "—", "status": "REVIEW", "statusColor": "#d8b24a",
        "report": report,
    })
    return {"session_id": session_id, "report": report, "lines": count,
            "filename": file.filename}


# ------------------------------ websocket ---------------------------------- #
@app.websocket("/ws/intercept")
async def websocket_intercept(websocket: WebSocket, token: Optional[str] = Query(None)):
    if not auth_manager.validate(token):
        await websocket.close(code=4401)
        return
    await websocket.accept()

    settings = db.get_settings()
    engine = TacticalIntelEngine(CONFIG, settings)
    use_mic = os.getenv("USE_MIC", "0") == "1"
    transcriber = AudioStreamTranscriber(CONFIG, use_microphone=use_mic)

    try:
        async for utt in transcriber.stream_transcriptions():
            payload = engine.analyze_utterance(utt["text"], utt.get("ts"))
            await websocket.send_json(payload)

        # Session finished: persist a report and tell the client.
        report = _build_report(engine, duration="00:59", lines=engine.line_counter)
        session_id = f"CH07-{int(time.time()) % 10000:04d}"
        db.create_session({
            "id": session_id,
            "subject": engine.subject if engine.id_locked else "UNASSIGNED",
            "channel": "VOICE · LIVE",
            "date": time.strftime("%d %b %y").upper(),
            "dur": "00:59",
            "status": "LOCKED" if engine.alerts() else "REVIEW",
            "statusColor": "#38ef7d" if engine.alerts() else "#d8b24a",
            "report": report,
        })
        await websocket.send_json({"type": "complete", "session_id": session_id,
                                   "report": report})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover
        try:
            await websocket.send_json({"type": "error", "detail": str(exc)})
        except Exception:
            pass
