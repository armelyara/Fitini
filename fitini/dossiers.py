"""
dossiers.py -- local, offline store for investigation dossiers.

A dossier groups heterogeneous sources (articles, posts, images, audio, docs)
gathered around a single subject. Everything lives on disk under
``dossiers/<id>/`` so downloaded evidence stays with its metadata and nothing
leaves the self-hosted server.

Layout:
    dossiers/<id>/dossier.json     # metadata + list of sources
    dossiers/<id>/sources/<sid>.*  # downloaded content (added by the collect step)
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

BASE_DIR = os.environ.get("FITINI_DATA", os.getcwd())  # runtime data: user's folder
DOSSIERS_DIR = os.environ.get("FITINI_DOSSIERS", os.path.join(BASE_DIR, "dossiers"))
_LOCK = threading.Lock()


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _safe_id(value: str) -> str:
    """Reject ids that could escape the data directory (path traversal)."""
    v = str(value or "")
    if ".." in v or "/" in v or "\\" in v or not _ID_RE.match(v):
        raise ValueError("invalid id: %r" % value)
    return v


def _ensure_root() -> None:
    os.makedirs(DOSSIERS_DIR, exist_ok=True)


def _dossier_dir(dossier_id: str) -> str:
    return os.path.join(DOSSIERS_DIR, _safe_id(dossier_id))


def _meta_path(dossier_id: str) -> str:
    return os.path.join(_dossier_dir(dossier_id), "dossier.json")


def _read_meta(dossier_id: str) -> Optional[Dict[str, Any]]:
    try:
        path = _meta_path(dossier_id)
    except ValueError:
        return None
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _write_meta(meta: Dict[str, Any]) -> None:
    # Atomic write: temp file then replace, so a crash never leaves a half file.
    d = _dossier_dir(meta["id"])
    os.makedirs(os.path.join(d, "sources"), exist_ok=True)
    tmp = _meta_path(meta["id"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, _meta_path(meta["id"]))


def new_id() -> str:
    return "DOS-" + uuid.uuid4().hex[:8].upper()


def create_dossier(name: str, operator: str = "") -> Dict[str, Any]:
    _ensure_root()
    meta = {
        "id": new_id(),
        "name": (name or "Sans titre").strip(),
        "created_at": time.time(),
        "operator": operator,
        "sources": [],
    }
    with _LOCK:
        _write_meta(meta)
    return meta


def list_dossiers() -> List[Dict[str, Any]]:
    _ensure_root()
    out: List[Dict[str, Any]] = []
    with _LOCK:
        for entry in os.listdir(DOSSIERS_DIR):
            meta = _read_meta(entry)
            if meta:
                out.append({
                    "id": meta["id"],
                    "name": meta.get("name", ""),
                    "created_at": meta.get("created_at", 0),
                    "count": len(meta.get("sources", [])),
                })
    out.sort(key=lambda d: d.get("created_at", 0), reverse=True)
    return out


def get_dossier(dossier_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return _read_meta(dossier_id)


def add_sources(dossier_id: str, sources: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Append saved sources to a dossier (used later by the collect step)."""
    with _LOCK:
        meta = _read_meta(dossier_id)
        if meta is None:
            return None
        meta.setdefault("sources", []).extend(sources)
        _write_meta(meta)
        return meta


def save_source_file(dossier_id: str, source_id: str, text: str) -> str:
    """Persist a downloaded source's text next to the dossier metadata."""
    d = os.path.join(_dossier_dir(dossier_id), "sources")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, _safe_id(source_id) + ".txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text or "")
    return path


def set_source_orientation(dossier_id: str, source_id: str, orientation: str):
    """Tag one source's political/editorial orientation (analyst-set)."""
    with _LOCK:
        meta = _read_meta(dossier_id)
        if meta is None:
            return None
        found = False
        for src in meta.get("sources", []):
            if src.get("id") == source_id:
                src["orientation"] = orientation
                found = True
                break
        if not found:
            return None
        _write_meta(meta)
        return meta
