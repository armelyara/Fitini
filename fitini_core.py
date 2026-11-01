"""
fitini_core.py — the domain-agnostic correlation engine behind Fitini.

Fitini is ONE real-time correlation engine. You point it at a domain by loading a
*domain profile* (profiles/*.json) that declares:
  * lexicon / patterns  — what entities to extract (keywords + regex), deterministic
  * links               — correlation rules that imply related entities (build a graph)
  * hypotheses          — scored candidates (targets, diagnoses, campaigns, incidents)
                          with weighted criteria and a lock threshold
  * alert_rules         — instant red-flag conditions

Across every domain the universal output is the same pipeline — extract → correlate →
score → lock → alert — plus the feature they all share: a running, timestamped
**timeline** (chronologie des faits) of everything the engine noticed and when.

Extraction here is a deterministic lexicon + regex (runs offline, fully explainable).
GLiNER zero-shot NER or an LLM extractor can be layered in later; the knowledge base
stays the source of truth so every hypothesis and alert can be justified.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


class Timeline:
    """Ordered, timestamped chronology of facts — the cross-domain backbone."""

    def __init__(self):
        self.events: List[Dict[str, Any]] = []
        self._seq = 0

    def add(self, kind: str, text: str, ts: Optional[str] = None,
            detail: str = "", source: Optional[str] = None) -> Dict[str, Any]:
        self._seq += 1
        ev = {"seq": self._seq, "t": ts, "kind": kind, "text": text,
              "detail": detail, "source": source}
        self.events.append(ev)
        return ev

    def export(self) -> List[Dict[str, Any]]:
        return list(self.events)


class CorrelationEngine:
    def __init__(self, profile: Dict[str, Any]):
        self.profile = profile
        self.labels = profile.get("labels", {})
        self.lexicon = profile.get("lexicon", [])
        self.patterns = [(re.compile(p["regex"]), p["label"], p.get("token"))
                         for p in profile.get("patterns", [])]
        self.links = profile.get("links", [])
        self.hypotheses = profile.get("hypotheses", [])
        self.alert_rules = profile.get("alert_rules", [])
        self.reset()

    def reset(self) -> None:
        self.entities: Dict[str, Dict[str, Any]] = {}     # canonical -> {category,count}
        self.locked: Optional[str] = None
        self.leading: Optional[str] = None
        self.line = 0
        self.timeline = Timeline()
        self._alerted: set = set()

    # ------------------------------------------------------------------ #
    def _extract(self, text: str) -> List[Dict[str, Any]]:
        lower = text.lower()
        spans: List[Dict[str, Any]] = []
        for lx in self.lexicon:
            pat = lx["pattern"].lower()
            i = lower.find(pat)
            while i != -1:
                spans.append({"start": i, "end": i + len(pat), "label": lx["label"],
                              "canonical": lx.get("canonical", lx["pattern"].upper()),
                              "display": text[i:i + len(pat)]})
                i = lower.find(pat, i + 1)
        for rx, label, token in self.patterns:
            for m in rx.finditer(text):
                spans.append({"start": m.start(), "end": m.end(), "label": label,
                              # correlation token is stable (e.g. "CVE"); display is the value
                              "canonical": token or label, "display": m.group(0)})
        spans.sort(key=lambda s: (s["start"], -(s["end"] - s["start"])))
        chosen, last_end = [], -1
        for s in spans:
            if s["start"] >= last_end:
                chosen.append(s)
                last_end = s["end"]
        return chosen

    def _segs(self, text: str, spans) -> List[Dict[str, Any]]:
        segs, cursor = [], 0
        for s in spans:
            if s["start"] > cursor:
                segs.append({"text": text[cursor:s["start"]], "label": None})
            segs.append({"text": text[s["start"]:s["end"]], "label": s["label"],
                         "canonical": s["canonical"]})
            cursor = s["end"]
        if cursor < len(text):
            segs.append({"text": text[cursor:], "label": None})
        return segs

    def _score(self) -> List[Dict[str, Any]]:
        out = []
        for h in self.hypotheses:
            total = sum(c["weight"] for c in h["criteria"]) or 1
            matched, present, missing = 0, [], []
            for c in h["criteria"]:
                e = c["entity"]
                if e in self.entities:
                    matched += c["weight"]
                    present.append(e)
                else:
                    missing.append({"entity": e, "question": c.get("question")})
            out.append({
                "name": h["name"], "code": h.get("code", ""),
                "score": matched, "confidence": round(matched / total, 2),
                "red_flag": bool(h.get("red_flag")),
                "lock_threshold": h.get("lock_threshold", 999),
                "present": present, "missing": missing,
            })
        out.sort(key=lambda d: (d["score"], d["confidence"]), reverse=True)
        return out

    # ------------------------------------------------------------------ #
    def analyze_utterance(self, text: str, ts: Optional[str] = None,
                          speaker: Optional[str] = None) -> Dict[str, Any]:
        self.line += 1
        spans = self._extract(text)
        segs = self._segs(text, spans)
        new_entities = []

        for sp in spans:
            canon = sp["canonical"]
            if canon not in self.entities:
                self.entities[canon] = {"category": sp["label"], "count": 0}
                self.timeline.add("entity", sp["display"], ts, detail=sp["label"], source=text)
                new_entities.append(canon)
            self.entities[canon]["count"] += 1

        # correlation links -> implied entities / graph edges
        for lk in self.links:
            w, a = lk["when"], lk["add"]
            if w in self.entities and a not in self.entities:
                self.entities[a] = {"category": lk.get("category", "ENTITY"), "count": 1}
                self.timeline.add("link", f"{w} → {a}", ts,
                                  detail=lk.get("relation", "correlated"), source=text)
                new_entities.append(a)

        diff = self._score()
        if diff:
            top = diff[0]
            if top["name"] != self.leading:
                self.leading = top["name"]
                self.timeline.add("lead", f"Leading: {top['name']}", ts,
                                  detail=f"{top['confidence']:.0%}")
            second = diff[1]["score"] if len(diff) > 1 else 0
            if top["score"] >= top["lock_threshold"] and top["score"] - second >= 1 \
                    and self.locked != top["name"]:
                self.locked = top["name"]
                self.timeline.add("lock", f"LOCKED · {top['name']}", ts, detail=top["code"])

        alerts = []
        for rule in self.alert_rules:
            if all(e in self.entities for e in rule["all_of"]) and rule["name"] not in self._alerted:
                self._alerted.add(rule["name"])
                self.timeline.add("alert", rule["name"], ts,
                                  detail=rule.get("message", ""), source=text)
                alerts.append(rule)

        questions = []
        seen = set()
        for h in diff[:3]:
            for m in h["missing"]:
                q = m.get("question")
                if q and q not in seen:
                    seen.add(q)
                    questions.append({"for": h["name"], "question": q})

        return {
            "utterance_id": f"utt-{self.line}", "speaker": speaker, "ts": ts,
            "raw_text": text, "segs": segs, "new_entities": new_entities,
            "hypotheses": diff,
            "summary": {
                "leading": diff[0]["name"] if diff else None,
                "leading_code": diff[0]["code"] if diff else None,
                "leading_confidence": diff[0]["confidence"] if diff else 0.0,
                "locked": self.locked,
                "entities": list(self.entities.keys()),
            },
            "suggested_questions": questions[:4],
            "alerts": alerts,
            "timeline": self.timeline.export(),
        }
