"""
intel_engine.py — Tactical intelligence / correlation engine for Fitini Voice Analyzer.

Given a transcribed utterance, this engine:
  * splits the text into highlightable segments (plain / keyword / locked-keyword),
  * updates a 6x2 correlation matrix,
  * locks the primary subject and geographic locations,
  * accumulates extracted entities for the session report,
  * raises a threat alert when a flagged asset keyword appears.

The matching layer is a configurable keyword/rule dictionary (loaded from config.json),
NOT a trained NER model. spaCy is used only as an *optional* augmentation to surface
additional named entities (people / places / orgs) when the library and a model are
installed. This keeps the honest distinction: the correlation logic is rule-based;
spaCy, if present, just adds supplementary entity candidates.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# spaCy is optional. We attempt to load it lazily and degrade gracefully.
_SPACY_NLP = None
_SPACY_TRIED = False


def _maybe_load_spacy():
    global _SPACY_NLP, _SPACY_TRIED
    if _SPACY_TRIED:
        return _SPACY_NLP
    _SPACY_TRIED = True
    try:
        import spacy  # type: ignore

        for model in ("en_core_web_trf", "en_core_web_sm"):
            try:
                _SPACY_NLP = spacy.load(model)
                break
            except Exception:
                continue
    except Exception:
        _SPACY_NLP = None
    return _SPACY_NLP


class TacticalIntelEngine:
    """Stateful correlation engine for a single intercept session."""

    def __init__(self, config: Dict[str, Any], settings: Optional[Dict[str, Any]] = None):
        self.config = config
        self.settings = settings or dict(config.get("default_settings", {}))

        self.kb: List[Dict[str, Any]] = config.get("knowledge_base", [])
        self.entity_meta: Dict[str, Any] = config.get("entity_meta", {})
        self.loc_threshold: int = int(config.get("locations_lock_threshold", 2))
        self.redact_patterns: List[str] = config.get("redact_patterns", [])

        self.use_spacy = bool(self.settings.get("ner_spacy", False))
        self.reset()

    # ------------------------------------------------------------------ #
    def reset(self) -> None:
        self.rows: List[List[str]] = [["", ""] for _ in range(6)]
        self.subject: str = "SEARCHING..."
        self.id_locked: bool = False
        self.locations: List[str] = []
        self.location_locked: bool = False
        self.line_counter: int = 0
        # Accumulated, de-duplicated entities across the whole session.
        self._entities: "Dict[str, Dict[str, Any]]" = {}
        self._alerts: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    def _redact(self, text: str) -> str:
        if not self.settings.get("redact", False) or not self.redact_patterns:
            return text
        out = text
        for pat in self.redact_patterns:
            out = re.sub(re.escape(pat), "█" * len(pat), out, flags=re.IGNORECASE)
        return out

    def _record_entity(self, name: str, category: str, confidence: float) -> None:
        # Keep the highest-confidence sighting of each entity.
        prev = self._entities.get(name)
        if prev is None or confidence > prev["confidence"]:
            self._entities[name] = {
                "name": name,
                "category": category,
                "confidence": round(float(confidence), 2),
            }

    # ------------------------------------------------------------------ #
    def analyze_utterance(self, text: str, ts: Optional[str] = None) -> Dict[str, Any]:
        """Analyse one transcribed line and return the UI-ready payload."""
        self.line_counter += 1
        lower = text.lower()

        # 1) Find every keyword hit from the configured knowledge base.
        hits: List[Dict[str, Any]] = []
        for kb in self.kb:
            pat = kb["pattern"].lower()
            start = lower.find(pat)
            while start != -1:
                hits.append({"start": start, "end": start + len(pat), "kb": kb})
                start = lower.find(pat, start + 1)
        hits.sort(key=lambda h: h["start"])

        # 2) Build display segments (plain text + highlighted keywords).
        segs: List[Dict[str, Any]] = []
        entities_this_line: List[Dict[str, Any]] = []
        alert: Optional[Dict[str, Any]] = None
        cursor = 0

        for h in hits:
            if h["start"] < cursor:
                continue  # overlapping match already consumed
            if h["start"] > cursor:
                segs.append({"text": self._redact(text[cursor:h["start"]]), "kind": "plain"})

            kb = h["kb"]
            raw = text[h["start"]:h["end"]]
            is_lock = bool(kb.get("arrow"))
            segs.append({
                "text": raw,
                "kind": "kw_lock" if is_lock else "kw_norm",
                "canonical": kb["canonical"],
            })

            # Correlation matrix update.
            r, c = kb["slot"]
            self.rows[r][c] = kb["canonical"]
            self._record_entity(kb["canonical"], kb.get("category", "ENTITY"),
                                kb.get("confidence", 0.75))
            entities_this_line.append({"name": kb["canonical"], "category": kb.get("category", "ENTITY")})

            # Paired cell (correlated value such as a location).
            if kb.get("pair"):
                pr, pc, pval = kb["pair"]
                self.rows[pr][pc] = pval
                meta = self.entity_meta.get(pval, {})
                self._record_entity(pval, meta.get("category", "PLACE"), meta.get("confidence", 0.8))
                if pval in ("UKRAINE", "BLACK SEA") and pval not in self.locations:
                    self.locations.append(pval)

            # Subject lock (respects the auto-lock setting).
            if kb.get("linked_subject") and self.settings.get("autolock", True):
                self.subject = kb["linked_subject"]
                self.id_locked = True
                meta = self.entity_meta.get(self.subject, {})
                self._record_entity(self.subject, meta.get("category", "SUBJECT"),
                                    meta.get("confidence", 0.9))

            # Flagged asset -> threat alert.
            if is_lock:
                alert = {
                    "code": kb["canonical"],
                    "detail": "Target asset relocation detected in live intercept.",
                    "confidence": kb.get("confidence", 0.9),
                    "window": "72:00:00",
                }
                self._alerts.append(alert)

            cursor = h["end"]

        if cursor < len(text):
            segs.append({"text": self._redact(text[cursor:]), "kind": "plain"})

        # Location lock once enough correlated places are found.
        if len(self.locations) >= self.loc_threshold:
            self.location_locked = True

        # 3) Optional spaCy augmentation (supplementary entities only).
        if self.use_spacy:
            nlp = _maybe_load_spacy()
            if nlp is not None:
                try:
                    for ent in nlp(text).ents:
                        if ent.label_ in ("PERSON", "GPE", "LOC", "ORG", "FAC"):
                            cat = {"PERSON": "PERSON", "GPE": "PLACE", "LOC": "PLACE",
                                   "ORG": "ORG", "FAC": "FACILITY"}[ent.label_]
                            self._record_entity(ent.text.upper(), cat, 0.6)
                except Exception:
                    pass

        return {
            "type": "utterance",
            "utterance_id": f"utt-{self.line_counter}",
            "ts": ts,
            "raw_text": text,
            "segs": segs,
            "entities": entities_this_line,
            "alert": alert,
            "state": self.snapshot(),
        }

    # ------------------------------------------------------------------ #
    def snapshot(self) -> Dict[str, Any]:
        return {
            "rows": [row[:] for row in self.rows],
            "subject": self.subject,
            "id_locked": self.id_locked,
            "locations": self.locations[:],
            "location_locked": self.location_locked,
            "active_cell": "cell-3-0",
        }

    def entities(self) -> List[Dict[str, Any]]:
        """All entities seen this session, highest confidence first."""
        return sorted(self._entities.values(), key=lambda e: e["confidence"], reverse=True)

    def alerts(self) -> List[Dict[str, Any]]:
        return self._alerts[:]
