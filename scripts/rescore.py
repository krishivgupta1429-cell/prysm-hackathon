#!/usr/bin/env python3
"""Built during the Meta Global AI Developer Hackathon (new work).

Rerun ONLY the scoring/writing calls of an analysis from inputs saved with DEBUG_SAVE_INPUTS=1, with a chosen model and
style setting (the perception stages are not run again).

  backend/.venv/bin/python scripts/rescore.py samples/_local/scoring_inputs/<file>.json --model muse-spark-1.3 --style 0 --out out.json

--style 1 appends backend/app/studio/coach_style.md to the prompts (same as COACH_STYLE=1). Costs real Meta tokens.
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")
os.environ["AI_PROVIDER"] = "meta"
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "backend" / ".env")
os.environ["AI_PROVIDER"] = "meta"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs")
    ap.add_argument("--model", default="muse-spark-1.3", help="muse-spark-1.3 (default) or muse-wonder-1.0")
    ap.add_argument("--style", type=int, default=0, help="1 = append the coach style addendum")
    ap.add_argument("--out", help="write {result, raw, stats} here")
    a = ap.parse_args()
    from app.studio.rescore import load_inputs, rescore
    out = asyncio.run(rescore(load_inputs(a.inputs), model=a.model, style=bool(a.style)))
    if a.out:
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(out["stats"]["metrics"], indent=1))


if __name__ == "__main__":
    main()
