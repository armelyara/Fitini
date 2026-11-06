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

import asyncio

import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
import time
from typing import Any, Dict, List, Optional

from fastapi import (Depends, FastAPI, File, Header, HTTPException, Query,
                     UploadFile, WebSocket, WebSocketDisconnect)
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from . import dossiers
from . import packs
from . import web_collect
from . import dossier_analyze
from . import dossier_export
from . import doc_ingest
from .audio_transcriber import AudioStreamTranscriber
from .auth import AuthManager
from .fitini_core import CorrelationEngine

PKG_DIR = os.path.dirname(__file__)                 # packaged assets live here
CONFIG_PATH = os.environ.get("FITINI_CONFIG", os.path.join(PKG_DIR, "config.json"))
STATIC_DIR = os.path.join(PKG_DIR, "static")
DATA_DIR = os.environ.get("FITINI_DATA", os.getcwd())  # runtime data: user's folder
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

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


class DossierBody(BaseModel):
    name: str


class CollectBody(BaseModel):
    candidates: List[Dict[str, Any]]


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


# ------------------------------ dossiers ----------------------------------- #
@app.get("/api/dossiers")
async def dossiers_list(_: str = Depends(require_auth)):
    return {"dossiers": dossiers.list_dossiers()}


@app.post("/api/dossiers")
async def dossiers_create(body: DossierBody, _: str = Depends(require_auth)):
    return {"dossier": dossiers.create_dossier(body.name, operator=auth_manager.operator_id)}


@app.get("/api/dossier/{dossier_id}")
async def dossier_detail(dossier_id: str, _: str = Depends(require_auth)):
    d = dossiers.get_dossier(dossier_id)
    if not d:
        raise HTTPException(status_code=404, detail="Dossier not found")
    return d


@app.get("/api/search")
async def web_search(subject: Optional[str] = Query(None),
                     query: Optional[str] = Query(None),
                     _: str = Depends(require_auth)):
    """Discover candidate web sources (opt-in; no download). Errors are surfaced."""
    try:
        return {"candidates": web_collect.search(subject or "", query or ""), "error": None}
    except web_collect.WebCollectError as exc:
        return {"candidates": [], "error": str(exc)}
    except Exception:
        logging.getLogger("fitini").exception("web search failed")
        return {"candidates": [], "error": "Recherche indisponible."}


@app.post("/api/dossier/{dossier_id}/analyze")
async def dossier_analyze_ep(dossier_id: str, selectors: Optional[str] = Query(None),
                             _: str = Depends(require_auth)):
    """Deterministic before/now juxtaposition + entities over the dossier sources."""
    d = dossiers.get_dossier(dossier_id)
    if not d:
        raise HTTPException(status_code=404, detail="Dossier not found")
    return dossier_analyze.analyze(d, selectors or "")


@app.get("/api/dossier/{dossier_id}/export")
async def dossier_export_ep(dossier_id: str, format: str = Query("md"),
                            selectors: Optional[str] = Query(None),
                            _: str = Depends(require_auth)):
    """Export a dossier (juxtaposition + entities + sources) as Markdown or PDF."""
    d = dossiers.get_dossier(dossier_id)
    if not d:
        raise HTTPException(status_code=404, detail="Dossier not found")
    analysis = dossier_analyze.analyze(d, selectors or "")
    name = (d.get("name") or "dossier").strip().replace(" ", "_")[:40] or "dossier"
    if format == "pdf":
        try:
            data = dossier_export.to_pdf(d, analysis)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return Response(content=data, media_type="application/pdf",
                        headers={"Content-Disposition": 'attachment; filename="%s.pdf"' % name})
    md = dossier_export.to_markdown(d, analysis)
    return Response(content=md, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="%s.md"' % name})


@app.post("/api/dossier/{dossier_id}/collect")
async def dossier_collect(dossier_id: str, body: CollectBody, _: str = Depends(require_auth)):
    """Download the checked candidates into the dossier (human-curated)."""
    if not dossiers.get_dossier(dossier_id):
        raise HTTPException(status_code=404, detail="Dossier not found")
    saved: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for c in body.candidates:
        src = dict(c)
        src["captured_at"] = time.time()
        src["status"] = "saved"
        url = c.get("url")
        try:
            if url:
                text = web_collect.fetch_text(url)
                src["raw_text"] = text
                src["content_hash"] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
                dossiers.save_source_file(dossier_id, src.get("id", ""), text)
        except Exception as exc:
            errors.append({"id": c.get("id"), "error": str(exc)})
            src["raw_text"] = ""
        saved.append(src)
    meta = dossiers.add_sources(dossier_id, saved)
    return {"dossier": meta, "saved": len(saved), "errors": errors}


# ---------------------------- engine helpers ------------------------------- #
def _parse_domains(domains_param: Optional[str]) -> List[str]:
    ids = [d.strip() for d in (domains_param or "").split(",") if d.strip()]
    return ids or ["justice"]  # the UI always sends a selection; fall back defensively


def _watchlist(subject_param: Optional[str]) -> List[str]:
    """Subject -> selectors: comma phrases kept whole, PLUS individual words (>=3).
    So "Election mi-mandat 2026 USA" yields each word as a matchable selector."""
    raw = subject_param or ""
    terms: List[str] = []
    seen = set()
    for phrase in raw.split(","):
        phrase = phrase.strip()
        if phrase and phrase.lower() not in seen:
            seen.add(phrase.lower())
            terms.append(phrase)
    for w in re.split(r"[^0-9A-Za-z\u00C0-\u017F'\-]+", raw):
        w = w.strip("-'")
        if len(w) >= 3 and w.lower() not in seen:
            seen.add(w.lower())
            terms.append(w)
    return terms


def _engine_for(domains_param: Optional[str], subject_param: Optional[str] = None) -> CorrelationEngine:
    ids = _parse_domains(domains_param)
    try:
        profile = packs.load_domains(ids)
    except Exception:
        profile = packs.load_domains(["justice"])
    use_ner = os.getenv("FITINI_NER", "1") != "0"
    return CorrelationEngine(profile, watchlist=_watchlist(subject_param), use_ner=use_ner)


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


async def _source_stream(source):
    """Stream an uploaded file through INTERCEPT: text transcripts line-by-line, or
    audio transcribed on the fly via faster-whisper."""
    path = os.path.join(UPLOAD_DIR, os.path.basename(source or ""))
    if not source or not os.path.isfile(path):
        return
    if doc_ingest.is_text_like(path):
        lines = doc_ingest.to_lines(doc_ingest.extract_text(path))
        interval = min(CONFIG.get("pipeline", {}).get("simulation_interval_seconds", 1.1), 0.5)
        for i, line in enumerate(lines):
            await asyncio.sleep(interval)
            yield {"ts": "L%d" % (i + 1), "text": line}
    else:
        transcriber = AudioStreamTranscriber(CONFIG, use_microphone=False)
        async for seg in transcriber.transcribe_file(path):
            yield seg


async def _sim_stream(domain_ids):
    """Yield the selected domain's demo scenario, paced like a live stream."""
    interval = CONFIG.get("pipeline", {}).get("simulation_interval_seconds", 1.1)
    for ln in packs.demo_lines(domain_ids):
        await asyncio.sleep(interval)
        yield {"ts": ln.get("ts"), "text": ln["text"], "speaker": ln.get("speaker")}


# Flagged by TYPE, not by value: any detected entity of these labels lights up
# (arrow drawn), with NO need for the user to pre-type the exact words. The WATCH
# subject is an optional priority layer on top, not the condition to flag.
GENERIC_FLAG_LABELS = {
    # detected named entities (NER + selectors) -- the actors of any content
    "PERSON", "ORG", "LOCATION", "PLACE", "GPE", "HANDLE",
    # intrinsically sensitive signals
    "WATCH", "MONEY", "IBAN", "COORD", "CODEWORD", "CVE",
    "WEAPON", "VIOLENCE", "HATE", "LANDGRAB", "ADVERSE_EFFECT",
    "FLAG", "MALWARE", "APT",
}


def _flag_canonicals(p, engine=None):
    """Which spoken words fired THIS frame -- generalized by type & relation, so it
    works for any subject, not just pre-written rules. A shown word flags if it:
      (a) triggered an alert this frame, or
      (b) newly satisfied a hypothesis criterion, or
      (c) is of a high-signal type (money, IBAN, coord, codeword, CVE, violence...),
          or is the user's watchlist subject (WATCH), or
      (d) is a NEW entity correlated this frame to the user's subject,
    plus propagation through links (a spoken word implies a flagged entity)."""
    segs = p.get("segs", [])
    shown = [s["canonical"] for s in segs if s.get("canonical")]
    shownset = set(shown)
    newset = set(p.get("new_entities", []))
    interest = set()
    # (a) alerts fired this frame
    for a in p.get("alerts", []):
        val = a.get("value")
        if val:
            for s in segs:
                if s.get("canonical") and s.get("text") == val:
                    interest.add(s["canonical"])
        for e in (a.get("all_of") or []):
            interest.add(e)
    # (b) newly satisfied hypothesis criteria
    for h in p.get("hypotheses", []):
        for e in (h.get("present") or []):
            if e in newset:
                interest.add(e)
    # (c) high-signal types (and the user's WATCH subject) shown this frame
    for s in segs:
        c = s.get("canonical")
        if c and (s.get("label") or "").upper() in GENERIC_FLAG_LABELS:
            interest.add(c)
    # (d) new entities correlated this frame to the user's subject (WATCH) or to interest
    watch_all = set()
    if engine is not None:
        watch_all = {c for c, v in engine.entities.items()
                     if (v.get("category") or "") == "WATCH"}
    anchor = watch_all | interest
    for pair in p.get("relationships", []):
        if len(pair) >= 2:
            a, b = pair[0], pair[1]
            if a in anchor and b in newset and b in shownset:
                interest.add(b)
            if b in anchor and a in newset and a in shownset:
                interest.add(a)
    # link propagation
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
@app.post("/api/upload")
async def upload(file: UploadFile = File(...), _: str = Depends(require_auth)):
    """Store an uploaded source (voice note / recording / transcript) for INTERCEPT."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    name = uuid.uuid4().hex + ext
    with open(os.path.join(UPLOAD_DIR, name), "wb") as out:
        out.write(await file.read())
    return {"source": name, "filename": file.filename}


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...),
                  domains: Optional[str] = Query(None),
                  subject: Optional[str] = Query(None),
                  _: str = Depends(require_auth)):
    engine = _engine_for(domains, subject)
    transcriber = AudioStreamTranscriber(CONFIG, use_microphone=False)

    suffix = os.path.splitext(file.filename or "")[1] or ".wav"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    alerts: List[Dict[str, Any]] = []
    count = 0
    error: Optional[str] = None
    note: Optional[str] = None
    try:
        tmp.write(await file.read())
        tmp.close()
        try:
            if doc_ingest.is_text_like(tmp.name):
                for ln in doc_ingest.to_lines(doc_ingest.extract_text(tmp.name)):
                    r = engine.analyze_utterance(ln)
                    alerts += r.get("alerts", [])
                    count += 1
                if count == 0:
                    note = "Aucun texte exploitable extrait du document."
            else:
                async for item in transcriber.transcribe_file(tmp.name):
                    r = engine.analyze_utterance(item["text"], item.get("ts"))
                    alerts += r.get("alerts", [])
                    count += 1
                if count == 0:
                    note = "Aucune parole transcrite depuis l'audio."
        except RuntimeError as exc:
            # doc_ingest raises RuntimeError with an operator-facing reason
            # (missing lib / unsupported format) -- safe to surface to the UI.
            error = str(exc)
        except Exception:
            # Unexpected failure (corrupt file / parser bug): keep internals
            # server-side, return a generic message (no path/stack leak).
            logging.getLogger("fitini").exception("analyze: extraction failed")
            error = "Erreur d'extraction du document."
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    report = _canonical_report(engine, alerts, duration="—")
    session_id = _persist_session(engine, report, channel="FILE · UPLOAD", dur="—")
    return {"session_id": session_id, "report": report, "lines": report["lines"],
            "filename": file.filename, "error": error, "note": note}


# ------------------------------ websocket ---------------------------------- #
@app.websocket("/ws/intercept")
async def websocket_intercept(websocket: WebSocket,
                              token: Optional[str] = Query(None),
                              domains: Optional[str] = Query(None),
                              subject: Optional[str] = Query(None),
                              source: Optional[str] = Query(None),
                              mic: Optional[str] = Query(None)):
    if not auth_manager.validate(token):
        await websocket.close(code=4401)
        return
    await websocket.accept()

    engine = _engine_for(domains, subject)
    ids = _parse_domains(domains)
    use_mic = os.getenv("USE_MIC", "0") == "1" or (mic or "").lower() in ("1", "true", "yes")
    alerts: List[Dict[str, Any]] = []
    if use_mic:
        stream_gen = AudioStreamTranscriber(CONFIG, use_microphone=True).stream_transcriptions()
    elif source:
        stream_gen = _source_stream(source)
    else:
        stream_gen = _sim_stream(ids)

    try:
        async for utt in stream_gen:
            payload = engine.analyze_utterance(utt["text"], utt.get("ts"), utt.get("speaker"))
            payload["type"] = "utterance"
            payload["flag_canonicals"] = _flag_canonicals(payload, engine)
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
