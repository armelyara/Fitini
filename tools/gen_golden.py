"""
gen_golden.py — capture the deterministic WebSocket frames the live intercept emits.

Baseline for the engine-fusion non-regression check: after swapping the engine
behind the web adapter, the regenerated golden file must be byte-identical.
Run from the project root:  python3 tools/gen_golden.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from intel_engine import TacticalIntelEngine
OUT = os.path.join(ROOT, "tests", "golden", "intercept_frames.json")


def build() -> dict:
    config = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    # Pin to config defaults so the golden is independent of the live DB settings.
    settings = dict(config.get("default_settings", {}))
    engine = TacticalIntelEngine(config, settings)

    frames = []
    for line in config.get("simulated_intercept", []):
        frames.append(engine.analyze_utterance(line["text"], line["ts"]))

    return {
        "_meta": "Golden baseline of /ws/intercept utterance frames (simulation mode).",
        "settings": settings,
        "frames": frames,
        "final_state": {
            "subject": engine.subject,
            "id_locked": engine.id_locked,
            "locations": engine.locations,
            "location_locked": engine.location_locked,
            "entities": engine.entities(),
            "alerts": engine.alerts(),
        },
    }


def main() -> None:
    golden = build()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(golden, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    print(f"wrote {OUT}")
    print(f"  frames: {len(golden['frames'])}  "
          f"| subject: {golden['final_state']['subject']}  "
          f"| alerts: {len(golden['final_state']['alerts'])}")


if __name__ == "__main__":
    main()
