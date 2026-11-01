# Fitini — Voice Analyzer

A real-time **audio → transcription → keyword-correlation** pipeline behind a tactical
"FUI" (fictional user interface). Audio is transcribed on the fly, keywords are highlighted
and correlated into a target/location matrix, and the live screen locks a subject and raises
threat alerts.

> **Note.** The "SIGINT / OSINT" styling is an aesthetic recreation of a cinematic
> surveillance screen. The intelligence layer is a small, configurable keyword/rule engine
> (`config.json`) — not a trained model or a real interception capability. The live-mic mode
> only transcribes audio you feed it; use it on your own voice / test recordings.

## Quick start (simulation mode — no ML, no mic)

```bash
pip install -r requirements.txt
uvicorn server:app --reload --port 8000
```

Open <http://localhost:8000>. Log in (demo passphrase: **`fitini`**), go to **INTERCEPT**,
and press **INITIATE INTERCEPT** — the scripted intercept streams in over a WebSocket, the
matrix fills, the subject locks, and the threat alert fires.

## Live microphone mode (optional)

Install the optional extras (see `requirements.txt`), then:

```bash
USE_MIC=1 uvicorn server:app --port 8000
```

This enables Silero VAD + faster-whisper. Model inference runs **off the asyncio event
loop** via `asyncio.to_thread`, so the WebSocket stays responsive.

## Architecture

| File | Role |
|------|------|
| `server.py` | FastAPI: auth, WebSocket `/ws/intercept`, file analyze, sessions / targets / settings, static serving |
| `intel_engine.py` | `TacticalIntelEngine` — config-driven keyword/NER + correlation, subject/location lock, threat alerts |
| `audio_transcriber.py` | `AudioStreamTranscriber` — simulation / microphone / file sources; inference off the event loop |
| `db.py` | SQLite persistence (archive, dossiers, settings), seeded on first run |
| `auth.py` | Token login for the access gate |
| `config.json` | Knowledge base, pipeline params, seed data, demo credential |
| `static/index.html` | Vanilla FUI — 8 screens, WebSocket client driving the live intercept |

## WebSocket contract (`/ws/intercept`)

Each transcribed line is pushed as:

```json
{
  "type": "utterance",
  "utterance_id": "utt-13",
  "ts": "09:11:56",
  "raw_text": "They're moving Flying Fowl in 72 hours.",
  "segs": [
    { "text": "They're moving ", "kind": "plain" },
    { "text": "Flying Fowl", "kind": "kw_lock", "canonical": "FLYING FOWL" },
    { "text": " in ", "kind": "plain" },
    { "text": "72 hours", "kind": "kw_norm", "canonical": "72 HOURS" }
  ],
  "entities": [ { "name": "FLYING FOWL", "category": "ASSET" } ],
  "alert": { "code": "FLYING FOWL", "confidence": 0.88, "window": "72:00:00",
             "detail": "Target asset relocation detected in live intercept." },
  "state": {
    "rows": [["ALEXANDER","AMIRI"], ["SECURITY","UKRAINE"], ...],
    "subject": "AMIRI", "id_locked": true,
    "locations": ["UKRAINE","BLACK SEA"], "location_locked": true,
    "active_cell": "cell-3-0"
  }
}
```

A final `{ "type": "complete", "session_id": "...", "report": { ... } }` closes the session
and persists a report readable from the **REPORT** / **ARCHIVE** screens.

`seg.kind` drives the transcript styling: `plain`, `kw_norm` (green highlight), `kw_lock`
(solid highlight + the dashed SVG arrow to the matrix).

## Configuration

Edit `config.json` to change the knowledge base, correlation rules, pipeline parameters, or
the scripted intercept. Live settings (VAD, diarization, auto-lock, PII redaction) are
toggled on the **CONFIG** screen and persist in the database; `autolock` and `redact` affect
the next intercept run.

## Fixes vs. the original dossier

- **Event loop no longer blocks** — VAD and Whisper calls are dispatched with
  `asyncio.to_thread` instead of running inline on the event loop.
- **Rules are configurable** — the knowledge base lives in `config.json`, not hardcoded.
- **Honest naming** — the engine is keyword/rule correlation; spaCy NER is an *optional*
  augmentation (`settings.ner_spacy`), not the core.
- **Graceful degradation** — the full app runs with only `fastapi` + `uvicorn`; the mic/ML
  path is optional and guarded.
- **Robustness** — token auth, WebSocket auth + clean disconnect handling, error frames.
