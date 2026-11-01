"""
corpus_analyze.py — batch analysis over a FOLDER of documents.

The "give me a folder, tell me what's going on" mode. Point it at a directory of text
files (audio would be transcribed first) with a domain profile; it runs the correlation
engine over every document and aggregates the results into one brief:

  * Overview         — what entities dominate, across how many documents
  * Hotspot docs     — which files to read first (highest signal)
  * Relationship net — who/what is connected to whom
  * Chronology       — dated statements ordered in time
  * Flags / alerts   — notable or anomalous items
  * Key statements   — the few passages actually worth reading, with their source

Everything here is deterministic and offline — it organizes and ranks evidence, with
every item traceable to its source document. The fluent "what's going on" narrative is a
separate, optional LLM step that must cite these same sources; it is not invented here.

Usage:
    python3 corpus_analyze.py <profile> <folder>
    python3 corpus_analyze.py investigation/osint_social samples/ci_social
"""

import glob
import json
import os
import re
import sys

from fitini_core import CorrelationEngine

HERE = os.path.dirname(__file__)
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}


def load_profile(name):
    path = name if os.path.isfile(name) else os.path.join(HERE, "profiles", name + ".json")
    return json.load(open(path, encoding="utf-8"))


def flag_labels(profile):
    return {r["on_label"] for r in profile.get("alert_rules", []) if "on_label" in r}


def date_key(s):
    s = s.lower()
    year = int(re.search(r"20\d{2}", s).group()) if re.search(r"20\d{2}", s) else 0
    month = next((MONTHS[m] for m in MONTHS if m in s), 0)
    return (year, month)


def lines_of(text):
    return [ln.strip() for ln in re.split(r"[\n.]+", text) if ln.strip()]


def analyze(profile, folder):
    flags = flag_labels(profile)
    files = sorted(glob.glob(os.path.join(folder, "*.txt")) + glob.glob(os.path.join(folder, "*.md")))
    ent_freq, rel_counts, alerts, passages, docs, dated = {}, {}, [], [], [], []
    global_eng = CorrelationEngine(profile)   # corpus-wide pattern + network

    for path in files:
        base = os.path.basename(path)
        text = open(path, encoding="utf-8").read()
        eng = CorrelationEngine(profile)
        d_alerts = 0
        for ln in lines_of(text):
            r = eng.analyze_utterance(ln)
            global_eng.analyze_utterance(ln)
            sig, found = 0, []
            for s in r["segs"]:
                if s.get("label"):
                    found.append(s["label"])
                    sig += 2 if s["label"] in flags else 1
                    if s["label"] == "DATE":
                        dated.append({"date": s["text"], "doc": base, "text": ln})
            for a in r["alerts"]:
                d_alerts += 1
                sig += 3
                alerts.append({"name": a["name"], "value": a.get("value"), "doc": base,
                               "urgency": a.get("urgency"), "text": ln})
            if found:
                passages.append({"score": sig, "doc": base, "text": ln})
        d_flags = sum(1 for m in eng.entities.values() if m["category"] in flags)
        for canon, meta in eng.entities.items():
            e = ent_freq.setdefault(canon, {"label": meta["category"], "count": 0, "docs": set()})
            e["count"] += meta["count"]
            e["docs"].add(base)
        for a, b in eng.relationships:
            rel_counts[frozenset((a, b))] = rel_counts.get(frozenset((a, b)), 0) + 1
        docs.append({"doc": base, "score": len(eng.entities) + d_flags * 2 + d_alerts * 3,
                     "entities": len(eng.entities), "flags": d_flags, "alerts": d_alerts})

    return {"files": files, "ent_freq": ent_freq, "rel_counts": rel_counts, "alerts": alerts,
            "passages": passages, "docs": docs, "dated": dated, "global": global_eng, "flags": flags}


def brief(profile, folder, a):
    print("=" * 80)
    print(f"CORPUS BRIEF · {profile['name']}")
    print(f"folder: {folder}  ·  {len(a['files'])} documents")
    print("=" * 80)

    print("\n▌ WHAT'S GOING ON (top entities by document spread)")
    top = sorted(a["ent_freq"].items(), key=lambda kv: (len(kv[1]["docs"]), kv[1]["count"]), reverse=True)
    for canon, m in top[:8]:
        print(f"   {len(m['docs'])} docs · {m['count']:>2}×  {canon:<16} ({m['label']})")

    locked = a["global"].locked
    if locked:
        print(f"\n▌ PATTERN DETECTED (corpus-wide): {locked}")

    print("\n▌ HOTSPOT DOCUMENTS (read these first)")
    for d in sorted(a["docs"], key=lambda d: d["score"], reverse=True)[:4]:
        print(f"   score {d['score']:>2}  {d['doc']}   ({d['entities']} entities, {d['flags']} flags, {d['alerts']} alerts)")

    print("\n▌ RELATIONSHIP NETWORK")
    for pair, n in sorted(a["rel_counts"].items(), key=lambda kv: kv[1], reverse=True)[:8]:
        x, y = list(pair)
        print(f"   {x}  —  {y}" + (f"  (x{n})" if n > 1 else ""))

    print("\n▌ CHRONOLOGIE DES FAITS")
    for ev in sorted(a["dated"], key=lambda e: date_key(e["date"])):
        print(f"   {ev['date']:<16} [{ev['doc']}]  {ev['text']}")

    print("\n▌ FLAGS / ANOMALIES")
    seen = set()
    for al in a["alerts"]:
        k = (al["name"], al["value"], al["doc"])
        if k in seen:
            continue
        seen.add(k)
        v = f": {al['value']}" if al.get("value") else ""
        print(f"   [{al['urgency']}] {al['name']}{v}  [{al['doc']}]")

    print("\n▌ KEY STATEMENTS (most meaningful passages)")
    uniq, best = {}, []
    for p in sorted(a["passages"], key=lambda p: p["score"], reverse=True):
        if p["text"] in uniq:
            continue
        uniq[p["text"]] = True
        best.append(p)
    for p in best[:6]:
        print(f"   ({p['score']}) [{p['doc']}] {p['text']}")

    print("\n" + "-" * 80)
    print("Deterministic evidence map above. A narrative 'what happened' summary is an")
    print("optional LLM step that must cite these documents — it is not generated here.")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    profile = load_profile(sys.argv[1])
    folder = sys.argv[2]
    brief(profile, folder, analyze(profile, folder))


if __name__ == "__main__":
    main()
