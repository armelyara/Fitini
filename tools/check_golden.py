"""
check_golden.py — regression gate for the intercept frames.

Regenerates the frames from the current code and diffs them against the committed
golden file (tests/golden/intercept_frames.json). Exit 0 = identical, 1 = drift.
After the engine fusion, point gen_golden.build() at the new engine+adapter and this
must still pass byte-for-byte. Run:  python3 tools/check_golden.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.gen_golden import build  # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "golden", "intercept_frames.json")


def main() -> int:
    current = json.loads(json.dumps(build(), ensure_ascii=False, sort_keys=True))
    if not os.path.exists(GOLDEN):
        print("no golden file — run tools/gen_golden.py first")
        return 1
    golden = json.load(open(GOLDEN, encoding="utf-8"))
    if current == golden:
        print(f"OK — {len(current['frames'])} frames match the golden baseline")
        return 0
    # Pinpoint the first differing frame for a readable failure.
    cf, gf = current.get("frames", []), golden.get("frames", [])
    for i in range(max(len(cf), len(gf))):
        c = cf[i] if i < len(cf) else None
        g = gf[i] if i < len(gf) else None
        if c != g:
            print(f"DRIFT at frame {i}:")
            print("  current:", json.dumps(c, ensure_ascii=False)[:400])
            print("  golden :", json.dumps(g, ensure_ascii=False)[:400])
            break
    else:
        print("DRIFT in settings/final_state (frames identical)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
