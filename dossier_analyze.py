"""
dossier_analyze.py -- deterministic analysis of a constituted dossier (no LLM).

Given a dossier's saved sources, it:
  * extracts the dated passages mentioning the subject / selectors -- the
    "before/now" juxtaposition the analyst reads to judge coherence, and
  * runs the open-extraction engine over all source texts for entities.

Fitini surfaces and aligns; the human judges.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

_SENT = re.compile(r"[^.!?\n]+[.!?]?")


def _selectors(dossier: Dict[str, Any], extra: str) -> List[str]:
    raw = (dossier.get("name", "") or "") + " " + (extra or "")
    out, seen = [], set()
    for t in re.split(r"[,\s]+", raw):
        t = t.strip()
        if len(t) >= 3 and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def _passages_for(text: str, selectors: List[str]) -> List[Dict[str, Any]]:
    out = []
    for m in _SENT.finditer(text or ""):
        s = m.group().strip()
        if not s:
            continue
        low = s.lower()
        hit = sorted({sel for sel in selectors if sel.lower() in low})
        if hit:
            out.append({"passage": s[:400], "matched": hit})
    return out


def _date_key(d: str) -> str:
    return (d or "").strip() or "9999"  # empty dates sort last


def analyze(dossier: Dict[str, Any], extra_selectors: str = "") -> Dict[str, Any]:
    selectors = _selectors(dossier, extra_selectors)
    sources = dossier.get("sources", [])

    # 1) dated juxtaposition passages (oldest first = "avant" -> "maintenant")
    passages: List[Dict[str, Any]] = []
    for src in sources:
        for p in _passages_for(src.get("raw_text", ""), selectors):
            passages.append({
                "date": src.get("published_date", ""),
                "title": src.get("title", ""),
                "url": src.get("url", ""),
                "platform": src.get("platform", ""),
                "orientation": src.get("orientation", "unknown"),
                "passage": p["passage"],
                "matched": p["matched"],
            })
    passages.sort(key=lambda x: _date_key(x["date"]))

    # 2) entities via the open-extraction engine (deterministic, no spaCy)
    entities: List[Dict[str, Any]] = []
    try:
        from fitini_core import CorrelationEngine
        import packs
        try:
            profile = packs.load_domains(["justice"])
        except Exception:
            profile = {"patterns": [], "lexicon": []}
        engine = CorrelationEngine(profile, watchlist=selectors, use_ner=False)
        for src in sources:
            for line in (src.get("raw_text", "") or "").splitlines():
                line = line.strip()
                if line:
                    engine.analyze_utterance(line)
        entities = [{"name": k, "category": v.get("category"), "count": v.get("count")}
                    for k, v in engine.entities.items()]
        entities.sort(key=lambda e: -(e.get("count") or 0))
        entities = entities[:40]
    except Exception:
        entities = []

    return {"selectors": selectors, "sources": len(sources),
            "passages": passages, "entities": entities}
