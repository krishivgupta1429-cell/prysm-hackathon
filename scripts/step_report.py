#!/usr/bin/env python3
"""Built during the Meta Global AI Developer Hackathon (new work).

Before/after table of the step-list defects, measured the way the page shows them: the steps of every parameter are produced by the
results page's own step parser (JavaScript, run in node) - the OLD parser (git HEAD~N version of Results.tsx) on the OLD saved
text and the NEW parser (working tree) on the NEW text.

  python scripts/step_report.py --before samples/_local/bakeoff --after samples/_local/step_fix [--old-ref <git ref>]

Counts per video (over all 10 parameters):
  truncated  quoted phrases cut mid-word by a character clip (validator rewrite quotes of exactly 50/60 characters) + steps cut off at the end
  merged     displayed items that contain more than one "Step N" marker
  split      displayed items that are not a numbered step (an unnumbered extra line, or a quote continued in the next item)
  <3 steps   scored parameters with fewer than 3 displayed steps;  fallback first: cards whose first item is a fallback line
"""
import argparse
import glob
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
SCORED = ["Hook & Opening", "Retention & Pacing", "Shareability", "Audio & Music", "Trend Alignment", "Visual Quality & Originality"]
JS_TAIL = "\nconst fs=require('fs');const d=JSON.parse(fs.readFileSync(0,'utf8'));console.log(JSON.stringify(d.map(t=>parseSteps(t))))\n"


def parser_js(results_tsx: str, steps_ts: str | None) -> str:
    i = results_tsx.index("function parseSteps")
    j = results_tsx.index("function getParamIcon")
    fn = results_tsx[i:j].replace("(text: string): string[]", "(text)")
    pre = ""
    if steps_ts:
        pre = steps_ts.replace("export function", "function").replace("(text: string): string[] | null", "(text)")
        pre = re.sub(r"^//.*$", "", pre, flags=re.M)
    return pre + "\n" + fn + JS_TAIL


def run_parser(js: str, texts: list) -> list:
    out = subprocess.run(["node", "-e", js], input=json.dumps(texts), capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(out.stderr[:500])
    return json.loads(out.stdout)


def counts(items_per_param: list, names: list, markers_stripped: bool = False) -> dict:
    trunc = merged = split = few = fb_first = 0
    per = []
    for name, items in zip(names, items_per_param):
        per.append(len(items))
        for k, it in enumerate(items):
            if len(re.findall(r"Step\s*\d+\s*[—–:\-]", it)) > 1:
                merged += 1
            numbered = markers_stripped or bool(re.match(r"\s*Step\s*\d+\s*[—–:\-]?", it))   # the new parser removes the markers: every item is one numbered step
            if not numbered or it[:1].islower():
                split += 1
            if re.search(r"already exists (?:in your voiceover at|at) [\d.]+s \('([^']{50}|[^']{60})'\)", it):
                trunc += 1
        if items and re.search(r"cut off|[a-z]'$", items[-1].strip()) and not re.search(r"[.!?…)'\"’”✨]$", items[-1].strip()):
            trunc += 1
        if name in SCORED and len(items) < 3:
            few += 1
        if items and re.match(r"\s*(?:Step\s*\d+\s*[—–:\-]\s*)?fallback\b", items[0], re.I):
            fb_first += 1
    return {"per": per, "truncated": trunc, "merged": merged, "split": split, "few": few, "fb_first": fb_first}


def load_before(folder: Path, video: str):
    f = [p for p in glob.glob(str(folder / f"{video}_*_spark13_style.json"))][0]
    return json.load(open(f))["result"]["parameters"]


def load_after(folder: Path, video: str):
    return json.load(open(folder / f"{video}.json"))["result"]["parameters"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", default=str(ROOT / "samples/_local/bakeoff"))
    ap.add_argument("--after", default=str(ROOT / "samples/_local/step_fix"))
    ap.add_argument("--old-ref", default="407b6a4", help="git ref whose Results.tsx holds the old step parser")
    a = ap.parse_args()
    old_tsx = subprocess.run(["git", "show", f"{a.old_ref}:web/src/pages/Results.tsx"], capture_output=True, text=True, cwd=ROOT).stdout
    new_tsx = (ROOT / "web/src/pages/Results.tsx").read_text()
    steps_ts = (ROOT / "web/src/studio/steps.ts").read_text()
    old_js, new_js = parser_js(old_tsx, None), parser_js(new_tsx, steps_ts)
    short = [n.split(" ")[0][:5] for n in ["Hook & Opening", "Retention & Pacing", "Shareability", "Audio & Music", "Trend Alignment",
                                           "Visual Quality & Originality", "On-Screen Text", "Save Potential", "Replay Value", "Call to Action"]]
    print("Steps shown per parameter (" + ", ".join(short) + ") and defect counts, as the results page shows them\n")
    print(f"| video | when | steps per parameter | truncated | merged | split / unnumbered | scored < 3 steps | fallback first |")
    print("|---|---|---|---|---|---|---|---|")
    for video in ("dance", "reel", "saree"):
        for when, params, js in (("before", load_before(Path(a.before), video), old_js), ("after", load_after(Path(a.after), video), new_js)):
            names = [p["name"] for p in params]
            items = run_parser(js, [p.get("howToDoIt") or "" for p in params])
            c = counts(items, names, markers_stripped=(when == "after"))
            print(f"| {video} | {when} | {' '.join(map(str, c['per']))} | {c['truncated']} | {c['merged']} | {c['split']} | {c['few']} | {c['fb_first']} |")


if __name__ == "__main__":
    main()
