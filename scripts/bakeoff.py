#!/usr/bin/env python3
"""Built during the Meta Global AI Developer Hackathon (new work).

Blind bake-off of the scoring/writing calls on saved inputs (DEBUG_SAVE_INPUTS=1), without re-running the perception stages.

Variants per video:  Spark 1.3 without style  |  Spark 1.3 with COACH_STYLE  |  Wonder with COACH_STYLE.
Which letter (X/Y/Z) is which variant is shuffled per video; the key is written to a separate file.

  backend/.venv/bin/python scripts/bakeoff.py --video saree=samples/_local/scoring_inputs/a.json --video dance=... --budget 3.0

Writes docs/bakeoff.md (hidden letters, full texts), docs/bakeoff-metrics.md (hidden letters),
docs/bakeoff-key.md and docs/bakeoff-metrics-keyed.md (the key), plus raw results under samples/_local/bakeoff (git-ignored).
Stops before a variant if the Meta spend so far plus that variant's estimated cost would exceed --budget.
"""
import argparse
import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")
os.environ["AI_PROVIDER"] = "meta"
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "backend" / ".env")
os.environ["AI_PROVIDER"] = "meta"

VARIANTS = {
    "spark13_plain": {"model": "muse-spark-1.3", "style": False, "label": "Spark 1.3, no style"},
    "spark13_style": {"model": "muse-spark-1.3", "style": True, "label": "Spark 1.3 + COACH_STYLE"},
    "wonder_style": {"model": "muse-wonder-1.0", "style": True, "label": "Wonder + COACH_STYLE"},
}
LETTERS = ["X", "Y", "Z"]
FIELDS = [("problem", "Problem"), ("fix", "Fix"), ("whatIsHappening", "What is happening"), ("whyItMatters", "Why it matters"),
          ("whatToChange", "What to change"), ("howToDoIt", "How to do it"), ("whatToExpect", "What to expect")]
OUT_RAW = ROOT / "samples" / "_local" / "bakeoff"


def md_metrics(rows: list, keyed: bool) -> str:
    head = ["Video", "Variant" if keyed else "Letter", "Model" if keyed else "", "Valid JSON", "Truncated fields", "Steps per scored param",
            "Scored params < 3 steps", "Avg words / problem", "Transcript quotes", "Advisory with ready wording", "Repeated closings",
            "Overall", "Latency (s)", "Tokens in", "Tokens out (incl. reasoning)", "of which reasoning", "Cost (USD)"]
    keep = [i for i, h in enumerate(head) if h or keyed]
    if not keyed:
        keep = [i for i, h in enumerate(head) if h]
    out = ["| " + " | ".join(head[i] for i in keep) + " |", "|" + "---|" * len(keep)]
    for r in rows:
        m = r["metrics"]
        steps = ", ".join(f"{v}" for v in m["steps_per_scored"].values())
        cells = [r["video"], r["variant_label"] if keyed else r["letter"], r["model"] if keyed else "", "yes" if m["valid_json"] else "NO", m["truncated_fields"],
                 steps, m["scored_with_fewer_than_3_steps"], m["avg_words_per_problem"], m["transcript_quotes"],
                 m["advisory_with_ready_wording"], m["repeated_closing_phrases"], m["overall"], m["latency_s"], f"{m['tokens_in']:,}",
                 f"{m['tokens_out']:,}", f"{m['reasoning_tokens']:,}", f"{m['cost_usd']:.4f}"]
        out.append("| " + " | ".join(str(cells[i]) for i in keep) + " |")
    return "\n".join(out)


def md_texts(video: str, runs: list) -> str:
    runs = sorted(runs, key=lambda r: r["letter"])
    names = [p["name"] for p in runs[0]["result"]["parameters"]]
    out = [f"## Video: {video}", ""]
    out.append("| Letter | Overall score | Scores per parameter |")
    out.append("|---|---|---|")
    for r in runs:
        sc = ", ".join(f"{p['name'].split(' ')[0]} {p.get('score')}" for p in r["result"]["parameters"])
        out.append(f"| {r['letter']} | {r['result'].get('overallScore')} | {sc} |")
    out.append("")
    for name in names:
        out += [f"### {video} — {name}", ""]
        for r in runs:
            p = next((q for q in r["result"]["parameters"] if q.get("name") == name), None)
            out.append(f"#### {r['letter']}  (score {p.get('score') if p else 'missing'})")
            if not p:
                out += ["_missing in this variant_", ""]
                continue
            for key, label in FIELDS:
                out.append(f"**{label}.** {p.get(key) or '_empty_'}")
                out.append("")
        out.append("---")
        out.append("")
    return "\n".join(out)


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", action="append", required=True, help="name=path/to/scoring_inputs.json (repeat)")
    ap.add_argument("--budget", type=float, default=3.0, help="stop before a variant if spend + its estimate would exceed this (USD)")
    ap.add_argument("--seed", type=int, default=20261103)
    ap.add_argument("--docs", default=str(ROOT / "docs"))
    a = ap.parse_args()
    from app.studio.rescore import load_inputs, rescore

    OUT_RAW.mkdir(parents=True, exist_ok=True)
    rng = random.Random(a.seed)
    spent = 0.0
    est = {"spark13_plain": 0.12, "spark13_style": 0.13, "wonder_style": 0.55}   # conservative per-variant guesses until a real one is measured
    all_runs, key_rows, stopped = {}, [], None
    for spec in a.video:
        name, path = spec.split("=", 1)
        inp = load_inputs(path)
        order = list(VARIANTS)
        rng.shuffle(order)
        mapping = dict(zip(LETTERS, order))
        key_rows.append((name, path, mapping))
        runs = []
        for letter, vid in mapping.items():
            v = VARIANTS[vid]
            if spent + est[vid] > a.budget:
                stopped = f"budget: spent ${spent:.2f}, next variant ({vid}) estimated ${est[vid]:.2f}, limit ${a.budget:.2f}"
                break
            print(f"[{name}] {letter} = {vid} ...", flush=True)
            t = time.time()
            try:
                out = await rescore(inp, model=v["model"], style=v["style"])
            except Exception as e:  # keep going: a failed variant is a result too
                print(f"  FAILED {type(e).__name__}: {str(e)[:200]}", flush=True)
                runs.append({"video": name, "letter": letter, "variant": vid, "variant_label": v["label"], "model": v["model"], "failed": str(e)[:300]})
                continue
            cost = out["stats"]["cost_usd"]
            spent += cost
            est[vid] = max(cost, est[vid] * 0.5) if cost else est[vid]
            print(f"  done in {time.time() - t:.0f}s  ${cost:.4f}  (spent ${spent:.4f})", flush=True)
            rec = {"video": name, "letter": letter, "variant": vid, "variant_label": v["label"], "model": out["stats"]["model"],
                   "result": out["result"], "raw": out["raw"], "stats": out["stats"], "metrics": out["stats"]["metrics"]}
            (OUT_RAW / f"{name}_{letter}_{vid}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1))
            runs.append(rec)
        all_runs[name] = runs
        if stopped:
            break

    docs = Path(a.docs)
    ok = {n: [r for r in rs if "result" in r] for n, rs in all_runs.items()}
    texts = ["# Blind bake-off: scoring and writing calls", "",
             "Built during the Meta Global AI Developer Hackathon (new work). Generated by `scripts/bakeoff.py`.", "",
             "Three variants of the same scoring inputs per video, labelled X / Y / Z (the mapping is different for each video and is NOT in this file; "
             "it is in `docs/bakeoff-key.md`). Read the three versions of each parameter side by side and pick; open the key only afterwards. "
             "The perception stages were not re-run: every variant answers the exact same prompts (the saved inputs of one real analysis); only the "
             "model and the style setting change. One run per variant, so some difference is ordinary run-to-run variation.", "",
             "Metrics (also hidden behind the letters): `docs/bakeoff-metrics.md`. No winner is stated here.", ""]
    for n, rs in ok.items():
        if len(rs) >= 2:
            texts.append(md_texts(n, rs))
    (docs / "bakeoff.md").write_text("\n".join(texts))
    rows = [r for rs in ok.values() for r in sorted(rs, key=lambda r: r["letter"])]
    note = ("One run per variant per video. Steps per scored param lists the six scored parameters in canonical order (Hook, Retention, Shareability, "
            "Audio, Trend, Visual). Tokens out include the hidden reasoning tokens (billed as output). Truncated fields = text that stops mid-sentence "
            "or empty required fields, plus replies that ended at the token limit.")
    (docs / "bakeoff-metrics.md").write_text("# Bake-off metrics (letters hidden)\n\nBuilt during the Meta Global AI Developer Hackathon (new work).\n\n" + note + "\n\n" + md_metrics(rows, keyed=False) + "\n")
    (docs / "bakeoff-metrics-keyed.md").write_text("# Bake-off metrics (KEYED: do not open before choosing)\n\nBuilt during the Meta Global AI Developer Hackathon (new work).\n\n" + note + "\n\n" + md_metrics(rows, keyed=True) + "\n")
    key = ["# Bake-off key", "", "Built during the Meta Global AI Developer Hackathon (new work). Do not open before you have chosen.", ""]
    for name, path, mapping in key_rows:
        key += [f"## {name}", f"Inputs: `{Path(path).name}`", "", "| Letter | Variant | Model | Style |", "|---|---|---|---|"]
        key += [f"| {l} | {VARIANTS[v]['label']} | {VARIANTS[v]['model']} | {'COACH_STYLE=1' if VARIANTS[v]['style'] else 'off'} |" for l, v in mapping.items()]
        key.append("")
    if stopped:
        key += [f"**Stopped early: {stopped}**", ""]
    (docs / "bakeoff-key.md").write_text("\n".join(key))
    (OUT_RAW / "spend.json").write_text(json.dumps({"bakeoff_spend_usd": round(spent, 4), "stopped": stopped}))
    print(f"bake-off Meta spend ${spent:.4f}" + (f"  STOPPED: {stopped}" if stopped else ""))


if __name__ == "__main__":
    asyncio.run(main())
