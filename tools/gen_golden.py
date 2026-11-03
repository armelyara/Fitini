"""
gen_golden.py — capture the deterministic CANONICAL output of fitini_core on a
fixed reference scenario (Path B baseline).

Runs the unified engine (CorrelationEngine) over the lawful-intercept pack and the
reference stream, and records each utterance's canonical AnalysisResult. This is the
non-regression anchor: the engine's output on this scenario must stay byte-identical.
Run from the project root:  python3 tools/gen_golden.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fitini_core import CorrelationEngine  # noqa: E402

PROFILE = os.path.join(ROOT, "profiles", "investigation", "intercept.json")
CONFIG = os.path.join(ROOT, "config.json")
OUT = os.path.join(ROOT, "tests", "golden", "intercept_frames.json")


def build() -> dict:
    profile = json.load(open(PROFILE, encoding="utf-8"))
    # Reference stream: the scripted intercept lines (deterministic, offline).
    lines = json.load(open(CONFIG, encoding="utf-8")).get("simulated_intercept", [])
    engine = CorrelationEngine(profile)
    frames = [engine.analyze_utterance(ln["text"], ln.get("ts")) for ln in lines]
    last = frames[-1] if frames else {}
    return {
        "_meta": "Golden baseline — canonical fitini_core output on the lawful-intercept reference stream.",
        "profile": profile.get("id"),
        "frames": frames,
        "final_summary": last.get("summary", {}),
        "final_relationships": last.get("relationships", []),
    }


def main() -> None:
    golden = build()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(golden, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    s = golden["final_summary"]
    print(f"wrote {OUT}")
    print(f"  frames: {len(golden['frames'])} | locked: {s.get('locked')} "
          f"| entities: {len(s.get('entities', []))}")


if __name__ == "__main__":
    main()
