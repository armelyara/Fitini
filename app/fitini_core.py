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


def combine_profiles(profiles, name="Combined", pid="combined"):
    """Merge several domain knowledge packs into one profile the engine runs.

    This is what the UI's mandatory domain selection maps onto: pick one or
    several domains -> their packs are combined into a single pipeline. Pure
    (no I/O): the app layer loads the JSON files and passes the dicts here.

    lexicon / patterns / links / hypotheses / alert_rules are concatenated
    (lexicon de-duplicated on (pattern, label)); labels are unioned; cooccurrence
    labels are unioned. stream_lines are per-scenario demo input, not merged.
    """
    labels, lexicon, seen = {}, [], set()
    patterns, links, hyps, alerts = [], [], [], []
    cooc_labels, cooc_rel = [], None
    for prof in profiles:
        labels.update(prof.get("labels", {}))
        for lx in prof.get("lexicon", []):
            key = (lx.get("pattern", "").lower(), lx.get("label"))
            if key not in seen:
                seen.add(key)
                lexicon.append(lx)
        patterns += prof.get("patterns", [])
        links += prof.get("links", [])
        hyps += prof.get("hypotheses", [])
        alerts += prof.get("alert_rules", [])
        c = prof.get("cooccurrence")
        if c:
            for lab in c.get("labels", []):
                if lab not in cooc_labels:
                    cooc_labels.append(lab)
            cooc_rel = cooc_rel or c.get("relation")
    combined = {"id": pid, "domain": "Multi", "name": name, "labels": labels,
                "lexicon": lexicon, "patterns": patterns, "links": links,
                "hypotheses": hyps, "alert_rules": alerts}
    if cooc_labels:
        combined["cooccurrence"] = {"labels": cooc_labels,
                                    "relation": cooc_rel or "co-mentioned"}
    return combined



# Generic selectors: run on EVERY stream, with no lexicon — so the engine tags
# arbitrary subjects (emails, phones, IPs, money, dates, handles, coords, CVE, IBAN).
GENERIC_SELECTORS = [
    (r"[\w.+-]+@[\w-]+\.[\w.-]+", "EMAIL", "EMAIL"),
    (r"https?://[^\s<>\"]+", "URL", "URL"),
    (r"\bCVE-\d{4}-\d{4,7}\b", "CVE", "CVE"),
    (r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "IP", "IP"),
    (r"(?:\+\d{1,3}[\s-]?)?\d{3}[\s-]?\d{3}[\s-]?\d{4}", "PHONE", "PHONE"),
    (r"@[A-Za-z0-9_]{2,}", "HANDLE", "HANDLE"),
    (r"-?\d{1,2}\.\d{3,},\s*-?\d{1,3}\.\d{3,}", "COORD", "COORD"),
    (r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", "IBAN", "IBAN"),
    (r"[$\u20ac\u00a3]\s?\d[\d .,]*|\b\d[\d .,]*\s?(?:FCFA|CFA|USD|EUR|euros?|dollars?|milliards?|millions?)\b", "MONEY", "MONEY"),
    (r"(?i)\b(?:janvier|f\u00e9vrier|mars|avril|mai|juin|juillet|ao\u00fbt|septembre|octobre|novembre|d\u00e9cembre|january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{0,2},?\s*\d{4}\b", "DATE", "DATE"),
    (r"\b(?:19|20)\d{2}\b", "DATE", "DATE"),
]


# Optional spaCy NER (server-side). Lazy-loaded, cached, graceful fallback: if no
# model is installed the engine runs on selectors+lexicon+watchlist only. This is
# what makes extraction OPEN -- it finds PERSON/ORG/LOCATION/DATE even for names
# never listed anywhere.
_NLP = None
_NLP_TRIED = False
_SPACY_MAP = {"PER": "PERSON", "PERSON": "PERSON", "ORG": "ORG",
              "LOC": "LOCATION", "GPE": "LOCATION", "FAC": "LOCATION",
              "DATE": "DATE", "MONEY": "MONEY"}


def _load_nlp():
    global _NLP, _NLP_TRIED
    if _NLP_TRIED:
        return _NLP
    _NLP_TRIED = True
    try:
        import spacy  # type: ignore
        for model in ("fr_core_news_lg", "fr_core_news_md", "fr_core_news_sm",
                      "en_core_web_trf", "en_core_web_sm"):
            try:
                _NLP = spacy.load(model)
                break
            except Exception:
                continue
    except Exception:
        _NLP = None
    return _NLP

_FOLD_MAP = {ord(a): b for a, b in {
    "à": "a", "â": "a", "ä": "a", "á": "a", "ã": "a", "å": "a",
    "ç": "c", "é": "e", "è": "e", "ê": "e", "ë": "e",
    "í": "i", "ì": "i", "î": "i", "ï": "i", "ñ": "n",
    "ó": "o", "ò": "o", "ô": "o", "ö": "o", "õ": "o",
    "ú": "u", "ù": "u", "û": "u", "ü": "u", "ý": "y", "ÿ": "y",
}.items()}


def _fold(s: str) -> str:
    """Accent-insensitive folding (length-preserving, so offsets stay valid)."""
    return s.translate(_FOLD_MAP)


class CorrelationEngine:
    def __init__(self, profile: Dict[str, Any], watchlist=None, use_ner=True):
        self.profile = profile
        self.labels = dict(profile.get("labels", {}))
        self.lexicon = list(profile.get("lexicon", []))
        # generic selectors (always on) + the profile's own regex patterns
        pats = list(GENERIC_SELECTORS) + [(p["regex"], p["label"], p.get("token"))
                                          for p in profile.get("patterns", [])]
        self.patterns = [(re.compile(rx), lab, tok) for rx, lab, tok in pats]
        self.links = profile.get("links", [])
        self.hypotheses = profile.get("hypotheses", [])
        self.alert_rules = list(profile.get("alert_rules", []))
        self.cooccur = profile.get("cooccurrence")
        self.use_ner = use_ner
        # user watchlist: arbitrary subject / selector terms the user is after.
        # They are tagged (label WATCH) and always flagged -> the system follows
        # the user's subject, not a pre-baked keyword list.
        self.watchlist = [str(t).strip() for t in (watchlist or []) if str(t).strip()]
        if self.watchlist:
            # the user's subject takes PRIORITY over any profile term with the same text
            watch_entries = [{"pattern": t.lower(), "label": "WATCH", "canonical": t.upper()}
                             for t in self.watchlist]
            self.lexicon = watch_entries + self.lexicon
            self.alert_rules = self.alert_rules + [{
                "name": "Sujet suivi", "on_label": "WATCH",
                "message": "Terme de la liste de surveillance mentionne.",
                "urgency": "URGENT"}]
            self.labels.setdefault("WATCH", "#bfa6ea")
        self.reset()

    def reset(self) -> None:
        self.entities: Dict[str, Dict[str, Any]] = {}     # canonical -> {category,count}
        self.labels_present: set = set()                   # categories seen (for by-label refs)
        self.relationships: List[List[str]] = []          # [[a, b], ...] relationship graph
        self._rel_set: set = set()
        self.locked: Optional[str] = None
        self.leading: Optional[str] = None
        self.line = 0
        self.timeline = Timeline()
        self._alerted: set = set()

    # ------------------------------------------------------------------ #
    def _extract(self, text: str) -> List[Dict[str, Any]]:
        lower = text.lower()
        folded = _fold(lower)
        spans: List[Dict[str, Any]] = []
        for lx in self.lexicon:
            pat = _fold(lx["pattern"].lower())
            i = folded.find(pat)
            while i != -1:
                spans.append({"start": i, "end": i + len(pat), "label": lx["label"],
                              "canonical": lx.get("canonical", lx["pattern"].upper()),
                              "display": text[i:i + len(pat)]})
                i = lower.find(pat, i + 1)
        for rx, label, _token in self.patterns:
            for m in rx.finditer(text):
                # regex entities key on the actual value (e.g. "CVE-2023-23397"); the
                # label (e.g. "CVE") is what hypotheses/alerts match on by presence.
                spans.append({"start": m.start(), "end": m.end(), "label": label,
                              "canonical": m.group(0), "display": m.group(0)})
        if self.use_ner:
            nlp = _load_nlp()
            if nlp is not None:
                try:
                    for ent in nlp(text).ents:
                        lab = _SPACY_MAP.get(ent.label_)
                        if lab:
                            spans.append({"start": ent.start_char, "end": ent.end_char,
                                          "label": lab, "canonical": ent.text.strip().upper(),
                                          "display": ent.text})
                except Exception:
                    pass
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

    def _present(self, ref: str) -> bool:
        """A criterion/alert reference is satisfied by a specific entity OR by any
        entity of that label being present (e.g. 'CVE', 'PHONE')."""
        return ref in self.entities or ref in self.labels_present

    def _score(self) -> List[Dict[str, Any]]:
        out = []
        for h in self.hypotheses:
            total = sum(c["weight"] for c in h["criteria"]) or 1
            matched, present, missing = 0, [], []
            for c in h["criteria"]:
                e = c["entity"]
                if self._present(e):
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
        here: List[tuple] = []       # (canonical, label, display) present in THIS utterance
        new_here: List[tuple] = []   # subset newly seen this utterance

        for sp in spans:
            canon = sp["canonical"]
            self.labels_present.add(sp["label"])
            if canon not in self.entities:
                self.entities[canon] = {"category": sp["label"], "count": 0}
                self.timeline.add("entity", sp["display"], ts, detail=sp["label"], source=text)
                new_entities.append(canon)
                new_here.append((canon, sp["label"], sp["display"]))
            self.entities[canon]["count"] += 1
            here.append((canon, sp["label"], sp["display"]))

        # correlation links -> implied entities / graph edges
        for lk in self.links:
            w, a = lk["when"], lk["add"]
            if w in self.entities and a not in self.entities:
                cat = lk.get("category", "ENTITY")
                self.entities[a] = {"category": cat, "count": 1}
                self.labels_present.add(cat)
                self.timeline.add("link", f"{w} → {a}", ts,
                                  detail=lk.get("relation", "correlated"), source=text)
                new_entities.append(a)
                here.append((a, cat, a))
                new_here.append((a, cat, a))

        # cooccurrence -> relationship graph (e.g. who-is-mentioned-with-whom)
        if self.cooccur:
            labels = set(self.cooccur.get("labels", []))
            nodes = [c for (c, lab, _d) in here if lab in labels]
            nodes = list(dict.fromkeys(nodes))
            for i in range(len(nodes)):
                for j in range(i + 1, len(nodes)):
                    key = frozenset((nodes[i], nodes[j]))
                    if key not in self._rel_set:
                        self._rel_set.add(key)
                        self.relationships.append([nodes[i], nodes[j]])
                        self.timeline.add("link", f"{nodes[i]} — {nodes[j]}", ts,
                                          detail=self.cooccur.get("relation", "co-mentioned"),
                                          source=text)

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
            if "on_label" in rule:
                # watchlist: fire the first time each distinct value of this label appears
                for canon, lab, disp in new_here:
                    if lab == rule["on_label"]:
                        key = f"{rule['name']}|{disp}"
                        if key not in self._alerted:
                            self._alerted.add(key)
                            self.timeline.add("alert", f"{rule['name']}: {disp}", ts,
                                              detail=rule.get("message", ""), source=text)
                            alerts.append({**rule, "value": disp})
            elif "all_of" in rule:
                if all(self._present(e) for e in rule["all_of"]) and rule["name"] not in self._alerted:
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
            "relationships": [list(r) for r in self.relationships],
            "suggested_questions": questions[:4],
            "alerts": alerts,
            "timeline": self.timeline.export(),
        }
