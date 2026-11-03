"""Does Muse Spark hear the audio track of a native video?

Usage (from backend/):
    .venv/bin/python -m app.studio.spark_audio_test [../samples/prepared]

Sends each <name>.720p.mp4 as a chat/completions video_url data URL (the format that worked in
spike.py) to Spark 1.1/1.2/1.3, 2 runs each, and compares the quoted words with the Whisper and
Voice Transcribe transcripts saved in asr_compare_whisper.txt. Writes spark_audio_test.txt.
The API key is read from backend/.env at runtime and never printed or written.
"""
import difflib
import re
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

from app.studio import spike

MODELS = ["muse-spark-1.1", "muse-spark-1.2", "muse-spark-1.3"]
RUNS = 2
PROMPT = ("Listen to the audio of this video. Quote the exact words spoken or sung in the first 8 seconds, "
          "say whether there is background music, and name the language. "
          "If you cannot hear any audio, say exactly: NO AUDIO.")
WORD = re.compile(r"\w+", re.UNICODE)


def words(text: str) -> list[str]:
    return [w.lower() for w in WORD.findall(text)]


def load_transcripts(path: Path) -> dict[str, dict[str, list[str]]]:
    """{wav name: {"voice": [run texts], "whisper": [run texts]}} from the side-by-side tables."""
    out: dict[str, dict[str, list[str]]] = {}
    current = None
    for line in path.read_text().splitlines():
        if re.fullmatch(r"Video-\d+\.wav", line.strip()):
            current = line.strip()
            out[current] = {"voice": [], "whisper": []}
        elif current and re.match(r"\| \d+ \|", line):
            cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
            if len(cells) == 3:
                out[current]["voice"].append(cells[1])
                out[current]["whisper"].append(cells[2])
        elif line.startswith("====") and current and out[current]["voice"]:
            current = None
    return out


def quoted(answer: str) -> str:
    """The words the model claims to hear: text inside quotes, else the whole answer."""
    parts = re.findall(r"[\"“«]([^\"”»]{3,})[\"”»]", answer)
    return " ".join(parts) if parts else answer


def coverage(claimed: str, transcripts: list[str]) -> float | None:
    """Share of the claimed words that occur somewhere in the reference transcripts."""
    cw = words(claimed)
    ref = {w for t in transcripts for w in words(t)}
    return sum(w in ref for w in cw) / len(cw) if cw and ref else None


def ask(model: str, vurl: str) -> dict:
    body = {"model": model, "messages": [{"role": "user", "content": [
        {"type": "text", "text": PROMPT}, {"type": "video_url", "video_url": {"url": vurl}}]}]}
    start = time.time()
    rec = {"ok": False, "status": None, "seconds": None, "answer": "", "tin": None, "tout": None,
           "cost": None, "error": None}
    try:
        r = httpx.post(f"{spike.BASE_URL}/chat/completions", headers={"Authorization": f"Bearer {spike.KEY}"},
                       json=body, timeout=spike.TIMEOUT)
        rec["seconds"] = round(time.time() - start, 1)
        rec["status"] = r.status_code
        if r.is_success:
            j = r.json()
            rec["ok"] = True
            rec["answer"] = spike.scrub(spike.extract_text(j)).strip()
            rec["tin"], rec["tout"] = spike.extract_usage(j)
            if rec["tin"] is not None:
                rec["cost"] = ((rec["tin"] or 0) * spike.SPARK_IN_PER_M + (rec["tout"] or 0) * spike.SPARK_OUT_PER_M) / 1e6
        else:
            rec["error"] = spike.scrub(r.text)[:600]
    except Exception as e:
        rec["seconds"] = round(time.time() - start, 1)
        rec["error"] = spike.scrub(f"{type(e).__name__}: {e}")[:600]
    return rec


def main() -> None:
    prep = Path(sys.argv[1] if len(sys.argv) > 1 else "../samples/prepared").expanduser().resolve()
    spike.KEY = (dotenv_values(spike.ENV_PATH).get("META_API_KEY") or "").strip()
    if not spike.KEY:
        sys.exit("META_API_KEY not found in backend/.env")
    refs = load_transcripts(prep / "asr_compare_whisper.txt")
    log: list[str] = []

    def say(msg: str = "") -> None:
        print(msg, flush=True)
        log.append(msg)

    say(f"prompt: {PROMPT}\n")
    rows = []
    for mp4 in sorted(prep.glob("*.720p.mp4")):
        reel = mp4.name.removesuffix(".720p.mp4")
        ref = refs.get(f"{reel}.wav", {"voice": [], "whisper": []})
        vurl = spike.data_url(mp4, "video/mp4")
        say("=" * 100 + f"\n{reel}\n" + "=" * 100)
        say(f"Voice Transcribe run 1: {ref['voice'][0] if ref['voice'] else 'n/a'}")
        say(f"Whisper run 1:         {ref['whisper'][0] if ref['whisper'] else 'n/a'}\n")
        for model in MODELS:
            runs = []
            for n in range(RUNS):
                rec = ask(model, vurl)
                runs.append(rec)
                say(f"--- {model} run {n + 1}: {'OK' if rec['ok'] else 'ERR'} status={rec['status']} {rec['seconds']}s "
                    f"tokens in/out={rec['tin']}/{rec['tout']} cost=${rec['cost'] if rec['cost'] is not None else 'n/a'}")
                say(rec["answer"] if rec["ok"] else f"error: {rec['error']}")
                say()
            oks = [r for r in runs if r["ok"]]
            no_audio = [("NO AUDIO" in r["answer"].upper()) for r in oks]
            claims = [quoted(r["answer"]) for r in oks]
            cov_v = [coverage(c, ref["voice"]) for c, na in zip(claims, no_audio) if not na]
            cov_w = [coverage(c, ref["whisper"]) for c, na in zip(claims, no_audio) if not na]
            cov_v = [c for c in cov_v if c is not None]
            cov_w = [c for c in cov_w if c is not None]
            if len(oks) == 2:
                sim = difflib.SequenceMatcher(None, words(claims[0]), words(claims[1])).ratio()
                agree = (no_audio[0] == no_audio[1]) and (all(no_audio) or sim >= 0.7)
            else:
                sim, agree = None, None
            rows.append({"reel": reel, "model": model, "no_audio": f"{sum(no_audio)}/{len(oks)}",
                         "cov_v": sum(cov_v) / len(cov_v) if cov_v else None,
                         "cov_w": sum(cov_w) / len(cov_w) if cov_w else None,
                         "agree": agree, "sim": sim, "cost": sum(r["cost"] or 0 for r in runs),
                         "tin": oks[0]["tin"] if oks else None})

    def pct(x):
        return "n/a" if x is None else f"{x:.0%}"

    say("=" * 100 + "\nSUMMARY (NO AUDIO = runs that said it; match = share of quoted words found in the transcript)\n" + "=" * 100)
    say(f"{'reel':<13}{'model':<16}{'NO AUDIO':<10}{'match VT':<10}{'match WH':<10}{'runs agree':<12}{'cost 2 runs':<12}{'tokens in':<10}")
    for r in rows:
        agree = "n/a" if r["agree"] is None else ("yes" if r["agree"] else f"no ({pct(r['sim'])})")
        say(f"{r['reel']:<13}{r['model']:<16}{r['no_audio']:<10}{pct(r['cov_v']):<10}{pct(r['cov_w']):<10}"
            f"{agree:<12}${r['cost']:<11.5f}{str(r['tin']):<10}")
    say(f"\ntotal cost: ${sum(r['cost'] for r in rows):.4f}")
    out = prep / "spark_audio_test.txt"
    out.write_text("\n".join(log) + "\n")
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
