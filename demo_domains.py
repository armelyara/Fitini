"""Run the SAME engine over three domain profiles and print each chronology.

Proves the thesis: one Fitini, many domains, one timeline. Deterministic, offline.
Prototype demo — illustrative content only.
"""
import glob
import json
import os
import sys

from fitini_core import CorrelationEngine

HERE = os.path.dirname(__file__)
KIND_MARK = {"entity": "·", "link": "→", "lead": "»", "lock": "■", "alert": "⚠"}


def run_profile(path):
    profile = json.load(open(path, encoding="utf-8"))
    eng = CorrelationEngine(profile)
    print("\n" + "#" * 80)
    print(f"# {profile.get('domain','')} / {profile['name']}  [{profile['id']}·{profile.get('stream','')}]")
    print("#" * 80)

    last = None
    for ln in profile.get("stream_lines", []):
        r = eng.analyze_utterance(ln["text"], ln.get("ts"), ln.get("speaker"))
        flagged = "".join((f"[{s['text']}]" if s.get("label") else s["text"]) for s in r["segs"])
        print(f"\n{ln.get('ts',''):>6}  {ln.get('speaker','')}: {flagged}")
        top = r["hypotheses"][0] if r["hypotheses"] else None
        if top:
            lock = "  LOCKED" if top["name"] == r["summary"]["locked"] else ""
            print(f"        leading: {top['name']} ({top['confidence']:.0%}, score {top['score']}){lock}")
        for a in r["alerts"]:
            print(f"        >>> ALERT [{a['urgency']}] {a['name']}")
        if r["suggested_questions"]:
            print(f"        next: {r['suggested_questions'][0]['question']}")
        last = r

    if last.get("relationships"):
        print("\n  ── RELATIONSHIP GRAPH ─────────────────────────────────────────────")
        for a, b in last["relationships"]:
            print(f"     {a}  —  {b}")

    print("\n  ── CHRONOLOGIE DES FAITS ──────────────────────────────────────────")
    for ev in last["timeline"]:
        m = KIND_MARK.get(ev["kind"], " ")
        t = ev["t"] or ""
        detail = f"   ({ev['detail']})" if ev["detail"] else ""
        print(f"   {t:>6}  {m} {ev['kind']:<6} {ev['text']}{detail}")
    s = last["summary"]
    print(f"\n  RESULT: locked = {s['locked']} | entities = {', '.join(s['entities'])}")


def main():
    args = sys.argv[1:]
    all_paths = sorted(glob.glob(os.path.join(HERE, "profiles", "**", "*.json"), recursive=True))
    if args:
        paths = [p for p in all_paths if any(a.rstrip(".json") in p for a in args)]
    else:
        paths = all_paths
    for p in paths:
        run_profile(p)


if __name__ == "__main__":
    main()
