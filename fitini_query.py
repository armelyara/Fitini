"""
fitini_query.py — the interactive analyst brain. Ask Fitini follow-up questions.

Fitini is not "input -> one report -> done". After the first brief you keep asking:
  "give me the chronology", "when did the land grab happen?", "what's the date of the
  testimony?", "who is connected to the main subject?", "anything about violence?".

An AnalystSession ingests a corpus (or a live stream) once, holds the extracted structure
(entities, relationships, dated events, passages, flags), and answers follow-up questions
against it — every answer traced to source documents.

Routing here is deterministic keyword intent (runs offline, explainable). When an Anthropic
API key is available, the same question set can be handled by an LLM that calls these
methods as tools and phrases the answer — but the methods below stay the source of truth,
so nothing is invented. This file is that tool layer + a deterministic router.
"""

import glob
import os
import re
import sys

from fitini_core import CorrelationEngine

HERE = os.path.dirname(__file__)
STOP = set("the a an of to in on at and or for is are was were be been this that with "
           "did does do what when who where which how why give me show find about any "
           "anything more you fitini happened happen date dates".split())
DATE_RE = re.compile(r"(?i)\b(?:january|february|march|april|may|june|july|august|"
                     r"september|october|november|december)\s+20\d{2}\b|\b20\d{2}\b")


class AnalystSession:
    def __init__(self, profile):
        self.profile = profile
        self.lexicon = profile.get("lexicon", [])
        self.flag_labels = {r["on_label"] for r in profile.get("alert_rules", []) if "on_label" in r}
        self.records = []        # [{doc, line}]
        self.dated = []          # [{date, doc, line}]
        self.ent_freq = {}       # canon -> {label, count, docs:set}
        self.rel = {}            # frozenset(a,b) -> count
        self.alerts = []
        self.global_eng = CorrelationEngine(profile)

    # ---- ingest -------------------------------------------------------- #
    def ingest_folder(self, folder):
        files = sorted(glob.glob(os.path.join(folder, "*.txt")) + glob.glob(os.path.join(folder, "*.md")))
        for path in files:
            self.ingest_text(open(path, encoding="utf-8").read(), os.path.basename(path))
        return self

    def ingest_text(self, text, doc):
        eng = CorrelationEngine(self.profile)
        for line in [l.strip() for l in re.split(r"[\n.]+", text) if l.strip()]:
            self.records.append({"doc": doc, "line": line})
            r = eng.analyze_utterance(line)
            self.global_eng.analyze_utterance(line)
            for m in DATE_RE.finditer(line):
                self.dated.append({"date": m.group(0), "doc": doc, "line": line})
            for a in r["alerts"]:
                self.alerts.append({"name": a["name"], "value": a.get("value"),
                                    "urgency": a.get("urgency"), "doc": doc, "line": line})
        for canon, meta in eng.entities.items():
            e = self.ent_freq.setdefault(canon, {"label": meta["category"], "count": 0, "docs": set()})
            e["count"] += meta["count"]
            e["docs"].add(doc)
        for a, b in eng.relationships:
            self.rel[frozenset((a, b))] = self.rel.get(frozenset((a, b)), 0) + 1

    # ---- helpers ------------------------------------------------------- #
    def _date_key(self, s):
        s = s.lower()
        months = ["january", "february", "march", "april", "may", "june", "july",
                  "august", "september", "october", "november", "december"]
        y = int(re.search(r"20\d{2}", s).group()) if re.search(r"20\d{2}", s) else 0
        mo = next((i for i, m in enumerate(months, 1) if m in s), 0)
        return (y, mo)

    def _keywords(self, q):
        return [w for w in re.findall(r"[a-z0-9]+", q.lower()) if w not in STOP and len(w) > 2]

    def _resolve_entity(self, q):
        ql = q.lower()
        for canon, meta in self.ent_freq.items():
            if canon.lower() in ql:
                return canon
        for lx in self.lexicon:                       # match a lexicon surface form
            if lx["pattern"].lower() in ql:
                return lx.get("canonical")
        return None

    # ---- tools (also callable directly) -------------------------------- #
    def chronology(self):
        if not self.dated:
            return "No dated statements found in this corpus."
        out = ["Chronology:"]
        seen = set()
        for ev in sorted(self.dated, key=lambda e: self._date_key(e["date"])):
            k = (ev["date"], ev["line"])
            if k in seen:
                continue
            seen.add(k)
            out.append(f"  {ev['date']:<16} [{ev['doc']}] {ev['line']}")
        return "\n".join(out)

    def when(self, q):
        kws = self._keywords(q)
        hits = [r for r in self.records if any(k in r["line"].lower() for k in kws)]
        dated = [h for h in hits if DATE_RE.search(h["line"])]
        if dated:
            out = ["Dates for that:"]
            for h in dated:
                d = DATE_RE.search(h["line"]).group(0)
                out.append(f"  {d}  [{h['doc']}] {h['line']}")
            return "\n".join(out)
        if hits:
            return "No explicit date in the matching statements:\n" + \
                   "\n".join(f"  [{h['doc']}] {h['line']}" for h in hits[:5])
        return "Nothing matching that in the corpus."

    def actors(self):
        people = [(c, m) for c, m in self.ent_freq.items()
                  if m["label"] in ("PERSON", "HANDLE", "ORG")]
        people.sort(key=lambda cm: (len(cm[1]["docs"]), cm[1]["count"]), reverse=True)
        if not people:
            return "No actors extracted."
        return "Main actors:\n" + "\n".join(
            f"  {c:<16} ({m['label']}, {len(m['docs'])} docs)" for c, m in people[:10])

    def network(self, q=""):
        ent = self._resolve_entity(q)
        edges = [(list(p), n) for p, n in self.rel.items()]
        if ent:
            edges = [(p, n) for p, n in edges if ent in p]
            if not edges:
                return f"No connections recorded for {ent}."
            out = [f"Connections for {ent}:"]
            for p, n in sorted(edges, key=lambda e: e[1], reverse=True):
                other = p[0] if p[1] == ent else p[1]
                out.append(f"  {ent} — {other}" + (f" (x{n})" if n > 1 else ""))
            return "\n".join(out)
        out = ["Relationship network:"]
        for p, n in sorted(edges, key=lambda e: e[1], reverse=True)[:10]:
            out.append(f"  {p[0]} — {p[1]}" + (f" (x{n})" if n > 1 else ""))
        return "\n".join(out)

    def about(self, q):
        kws = self._keywords(q)
        hits = [r for r in self.records if any(k in r["line"].lower() for k in kws)]
        if not hits:
            return "Nothing matching that."
        return f"{len(hits)} statement(s):\n" + \
               "\n".join(f"  [{h['doc']}] {h['line']}" for h in hits[:8])

    def flags(self):
        if not self.alerts:
            return "No flags raised."
        out, seen = ["Flags / anomalies:"], set()
        for a in self.alerts:
            k = (a["name"], a["value"], a["doc"])
            if k in seen:
                continue
            seen.add(k)
            v = f": {a['value']}" if a.get("value") else ""
            out.append(f"  [{a['urgency']}] {a['name']}{v}  [{a['doc']}]")
        return "\n".join(out)

    def summary(self):
        top = sorted(self.ent_freq.items(),
                     key=lambda kv: (len(kv[1]["docs"]), kv[1]["count"]), reverse=True)
        out = [f"Overview — {len({r['doc'] for r in self.records})} documents."]
        out.append("Top entities: " + ", ".join(f"{c} ({len(m['docs'])})" for c, m in top[:6]))
        if self.global_eng.locked:
            out.append(f"Pattern detected: {self.global_eng.locked}")
        out.append(f"{len(self.alerts)} flag(s) raised.")
        return "\n".join(out)

    # ---- router -------------------------------------------------------- #
    def ask(self, q):
        ql = q.lower()
        if any(w in ql for w in ("chronolog", "timeline", "order of events")):
            return self.chronology()
        if "when" in ql or "date" in ql or "what time" in ql:
            return self.when(q)
        if any(w in ql for w in ("connect", "network", "linked", "relationship", "tied to", "associated")):
            return self.network(q)
        if any(w in ql for w in ("who", "actor", "people", "main", "suspect")):
            return self.actors()
        if any(w in ql for w in ("flag", "anomal", "alert", "suspicious", "hate", "violence")):
            return self.flags()
        if any(w in ql for w in ("summary", "overview", "going on", "what happened", "gist")):
            return self.summary()
        return self.about(q)   # default: search the corpus


if __name__ == "__main__":
    import json
    prof = sys.argv[1] if len(sys.argv) > 1 else "investigation/social"
    folder = sys.argv[2] if len(sys.argv) > 2 else "samples/ci_social"
    profile = json.load(open(os.path.join(HERE, "profiles", prof + ".json"), encoding="utf-8"))
    s = AnalystSession(profile).ingest_folder(os.path.join(HERE, folder))

    conversation = [
        "What's going on?",
        "Give me the chronology.",
        "When did the land grab happen?",
        "What's the date of the testimony?",
        "Who are the main actors?",
        "Who is connected to the main subject?",
        "Anything about violence in Gagnoa?",
    ]
    for q in conversation:
        print("\n\033[1m> " + q + "\033[0m")
        print(s.ask(q))
