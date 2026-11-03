"""Stage 0 spike: probe the Meta API (Muse Spark + Voice Transcribe) on a prepared reel.

Usage (from backend/):
    .venv/bin/python -m app.studio.spike ../samples/prepared/Video-54720

The argument is a prepared-file stem: <stem>_NN.jpg frames, <stem>.720p.mp4, <stem>.wav.
Voice Transcribe only, on every wav in a folder (3 runs each):
    .venv/bin/python -m app.studio.spike --asr ../samples/prepared
Results go to spike_results.json next to the stem. The API key is read from
backend/.env and is never printed or written.
"""
import base64
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

BASE_URL = "https://api.meta.ai/v1"
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
SPARK_IN_PER_M = 1.25
SPARK_OUT_PER_M = 4.25
TIMEOUT = 300.0

FRAMES_PROMPT = (
    "These are frames at 1 per second. This is a short vertical social video. "
    "Describe the hook in the first 3 seconds and list the 3 weakest moments "
    "with approximate timestamps. Be brief."
)
VIDEO_PROMPT = (
    "This is a short vertical social video. Describe the hook in the first 3 seconds "
    "and list the 3 weakest moments with approximate timestamps. Be brief."
)
AUDIO_PROMPT = (
    "Describe this audio: is it speech or music, what language, and are there "
    "any quality problems? Be brief."
)

KEY = ""
results: list[dict] = []


def scrub(text: str) -> str:
    return text.replace(KEY, "***") if KEY else text


def data_url(path: Path, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def extract_text(body) -> str:
    """Pull the model's text out of a chat/completions, /responses or ASR payload."""
    if not isinstance(body, dict):
        return str(body)
    if body.get("choices"):
        content = (body["choices"][0].get("message") or {}).get("content")
        if isinstance(content, list):
            return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        return content or ""
    if body.get("output_text"):
        return body["output_text"]
    if body.get("output"):
        parts = []
        for item in body["output"]:
            for c in item.get("content", []) if isinstance(item, dict) else []:
                if isinstance(c, dict) and c.get("text"):
                    parts.append(c["text"])
        if parts:
            return " ".join(parts)
    if body.get("text"):
        return body["text"]
    return json.dumps(body)


def extract_usage(body) -> tuple[int | None, int | None]:
    u = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(u, dict):
        return None, None
    tin = u.get("prompt_tokens", u.get("input_tokens"))
    tout = u.get("completion_tokens", u.get("output_tokens"))
    return tin, tout


def call(group: str, label: str, model: str | None, method: str, path: str,
         *, json_body=None, files=None, data=None, spark: bool = True) -> dict:
    """One API call. Never raises; always appends a result record and prints one line."""
    rec = {"group": group, "label": label, "model": model, "endpoint": f"{method} {path}",
           "ok": False, "status": None, "seconds": None, "tokens_in": None,
           "tokens_out": None, "cost_usd": None, "preview": "", "error": None}
    start = time.time()
    try:
        r = httpx.request(method, BASE_URL + path, headers={"Authorization": f"Bearer {KEY}"},
                          json=json_body, files=files, data=data, timeout=TIMEOUT)
        rec["seconds"] = round(time.time() - start, 2)
        rec["status"] = r.status_code
        try:
            body = r.json()
        except Exception:
            body = None
        raw = r.text
        rec["ok"] = r.is_success
        if body is not None:
            rec["tokens_in"], rec["tokens_out"] = extract_usage(body)
        if r.is_success:
            rec["preview"] = scrub(extract_text(body) if body is not None else raw)[:300]
            rec["raw"] = scrub(json.dumps(body) if body is not None else raw)[:6000]
        else:
            rec["error"] = scrub(raw)[:600]
            rec["preview"] = rec["error"][:300]
        if spark and rec["tokens_in"] is not None:
            rec["cost_usd"] = round(((rec["tokens_in"] or 0) * SPARK_IN_PER_M
                                     + (rec["tokens_out"] or 0) * SPARK_OUT_PER_M) / 1e6, 6)
    except Exception as e:
        rec["seconds"] = round(time.time() - start, 2)
        rec["error"] = scrub(f"{type(e).__name__}: {e}")[:600]
        rec["preview"] = rec["error"][:300]
    results.append(rec)
    cost = f"${rec['cost_usd']:.5f}" if rec["cost_usd"] is not None else "n/a"
    print(f"[{'OK ' if rec['ok'] else 'ERR'}] {group} | {label} | {model} | status={rec['status']} "
          f"| {rec['seconds']}s | tokens in/out={rec['tokens_in']}/{rec['tokens_out']} | cost={cost}")
    print(f"      {rec['preview'][:300]!r}\n")
    return rec


def chat(content: list, model: str) -> dict:
    return {"model": model, "messages": [{"role": "user", "content": content}]}


def responses(content: list, model: str) -> dict:
    return {"model": model, "input": [{"role": "user", "content": content}]}


def pick_models(ids: list[str]) -> tuple[dict[str, str], str | None]:
    """Find Spark 1.1 / 1.3 and the Voice Transcribe model ids from /models."""
    spark = {}
    for version in ("1.1", "1.3"):
        pat = re.compile(r"spark.*" + re.escape(version).replace(r"\.", r"[._-]"), re.I)
        match = sorted((i for i in ids if pat.search(i)), key=len)  # plain id before -contributor etc.
        if match:
            spark[version] = match[0]
    transcribe = next((i for i in ids if re.search(r"transcri|asr|voice", i, re.I)), None)
    return spark, transcribe


ASR_URLS = ["https://api.meta.ai/v1/asr/transcribe", "https://api.meta.ai/asr/transcribe"]
ASR_REQUEST = ('{"mode":"PUSH_TO_TALK","model":"muse-voice-transcribe-1.0","audioEncoding":"WAV",'
               '"partialMode":"CUMULATIVE","emitAudioProgress":false}')
ASR_USD_PER_HOUR = 0.18
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


def asr_transcribe(wav: Path, model: str = "muse-voice-transcribe-1.0", request_body: str | None = None,
                   urls: list[str] | None = None) -> dict:
    """Voice Transcribe as the Meta playground sends it: multipart, parts `request` and `audio` only.
    Falls through to the next URL if one errors. Never raises."""
    body = request_body or ASR_REQUEST.replace("muse-voice-transcribe-1.0", model)
    out = {"url": None, "ok": False, "status": None, "seconds": None, "audio_ms": None, "cost_usd": None,
           "transcript": None, "turns": None, "session": None, "error": None, "tried": []}
    audio = wav.read_bytes()
    for url in urls or ASR_URLS:
        out["url"] = url
        start = time.time()
        try:
            r = httpx.post(url, headers={"Authorization": f"Bearer {KEY}"}, timeout=TIMEOUT, files={
                "request": ("request.json", body, "application/json"),
                "audio": (wav.name, audio, "audio/wav")})
            out["seconds"] = round(time.time() - start, 2)
            out["status"] = r.status_code
            if r.is_success:
                j = r.json()
                out.update(ok=True, error=None, transcript=j.get("transcript"), turns=j.get("turns"),
                           session=j.get("sessionId"), audio_ms=j.get("audioDurationMs"))
                if out["audio_ms"] is not None:
                    out["cost_usd"] = round(out["audio_ms"] / 3_600_000 * ASR_USD_PER_HOUR, 6)
                return out
            out["error"] = scrub(r.text)
        except Exception as e:
            out["seconds"] = round(time.time() - start, 2)
            out["error"] = scrub(f"{type(e).__name__}: {e}")
        out["tried"].append({"url": url, "status": out["status"], "error": out["error"]})
        print(f"  ERR {url} status={out['status']} full error body: {out['error']}")
    return out


def groq_transcribe(wav: Path, groq_key: str) -> dict:
    """Whisper large-v3-turbo on Groq, language auto-detected (no `language` field sent)."""
    start = time.time()
    try:
        r = httpx.post(GROQ_URL, headers={"Authorization": f"Bearer {groq_key}"}, timeout=TIMEOUT,
                       files={"file": (wav.name, wav.read_bytes(), "audio/wav")},
                       data={"model": "whisper-large-v3-turbo", "response_format": "verbose_json"})
        j = r.json() if r.is_success else {}
        return {"ok": r.is_success, "status": r.status_code, "seconds": round(time.time() - start, 2),
                "text": (j.get("text") or "").strip(), "language": j.get("language"),
                "error": None if r.is_success else r.text.replace(groq_key, "***")[:600]}
    except Exception as e:
        return {"ok": False, "status": None, "seconds": round(time.time() - start, 2), "text": "",
                "language": None, "error": f"{type(e).__name__}: {e}".replace(groq_key, "***")}


def diff_summary(texts: list[str]) -> str:
    import difflib
    if len(set(texts)) == 1:
        return "identical across runs"
    lines = []
    for i in range(1, len(texts)):
        a, b = texts[0].split(), texts[i].split()
        sm = difflib.SequenceMatcher(None, a, b)
        changes = [f"{' '.join(a[i1:i2])!r} -> {' '.join(b[j1:j2])!r}"
                   for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]
        lines.append(f"run1 vs run{i + 1}: word similarity {sm.ratio():.3f}, {len(changes)} differing spans"
                     + ("".join(f"\n      {c}" for c in changes[:6])))
    return "\n    ".join(lines)


def mean_similarity(texts: list[str]) -> float | None:
    """Mean word-level similarity of run 1 against each later run (same metric as diff_summary)."""
    import difflib
    if len(texts) < 2:
        return None
    sims = [difflib.SequenceMatcher(None, texts[0].split(), t.split()).ratio() for t in texts[1:]]
    return sum(sims) / len(sims)


def asr_compare(prep_dir: Path) -> None:
    """Voice Transcribe on every wav (3 runs each), mode probing, and Groq Whisper if a key exists."""
    global KEY
    env = dotenv_values(ENV_PATH)
    KEY = (env.get("META_API_KEY") or "").strip()
    groq_key = (env.get("GROQ_API_KEY") or os.environ.get("GROQ_API_KEY") or "").strip()
    if not KEY:
        sys.exit("META_API_KEY not found in backend/.env")
    log = []

    def say(msg: str = "") -> None:
        print(msg)
        log.append(msg)

    wavs = sorted(prep_dir.glob("*.wav"))
    say(f"wav files: {[w.name for w in wavs]}\nGROQ_API_KEY set: {bool(groq_key)}\n")
    groq_runs: dict[str, list[dict]] = {}
    meta_runs: dict[str, list[str]] = {}
    for wav in wavs:
        say("=" * 100 + f"\n{wav.name}\n" + "=" * 100)
        runs = []
        for n in range(3):
            r = asr_transcribe(wav)
            runs.append(r)
            say(f"run {n + 1}: {'OK' if r['ok'] else 'ERR'} url={r['url']} status={r['status']} {r['seconds']}s "
                f"audioDurationMs={r['audio_ms']} est_cost=${r['cost_usd'] if r['cost_usd'] is not None else 'n/a'}")
            if not r["ok"]:
                say(f"  error: {r['error']}")
        if runs[0]["ok"]:
            say(f"\ntranscript (run 1):\n{runs[0]['transcript']}")
            say(f"\nturns (run 1):\n{json.dumps(runs[0]['turns'], ensure_ascii=False, indent=2)}")
            texts = [r["transcript"] or "" for r in runs if r["ok"]]
            say(f"\nrun-to-run diff ({len(texts)} ok runs):\n    {diff_summary(texts)}")
        if groq_key:
            gruns = []
            for n in range(3):
                g = groq_transcribe(wav, groq_key)
                gruns.append(g)
                say(f"\nGroq whisper-large-v3-turbo run {n + 1}: {'OK' if g['ok'] else 'ERR'} status={g['status']} "
                    f"{g['seconds']}s language={g['language']}" + (f" error={g['error']}" if g["error"] else ""))
                say(g["text"])
            gtexts = [g["text"] for g in gruns if g["ok"]]
            if gtexts:
                say(f"\nWhisper run-to-run diff ({len(gtexts)} ok runs):\n    {diff_summary(gtexts)}")
            groq_runs[wav.name] = gruns
            meta_runs[wav.name] = [r["transcript"] or "" for r in runs if r["ok"]]
        say()

    # mode probing on the first wav: report the API's error text, max 3 guesses
    if wavs and not groq_key:
        say("=" * 100 + "\nMode probing (first wav)\n" + "=" * 100)
        for mode in ("CONTINUOUS", "STREAMING", "INVALID_MODE_PROBE"):
            body = ASR_REQUEST.replace("PUSH_TO_TALK", mode)
            r = asr_transcribe(wavs[0], request_body=body, urls=[ASR_URLS[0]])
            say(f"mode={mode}: {'OK' if r['ok'] else 'ERR'} status={r['status']} "
                + (f"transcript={(r['transcript'] or '')[:120]!r}" if r["ok"] else f"error={r['error']}"))
        say()

    if groq_key:
        say("=" * 100 + "\nSide by side: Voice Transcribe vs Whisper (every run)\n" + "=" * 100)
        for name, vt in meta_runs.items():
            gt = [g["text"] for g in groq_runs[name] if g["ok"]]
            say(f"\n{name}\n| Run | Voice Transcribe | Whisper large-v3-turbo |\n|---|---|---|")
            for i in range(max(len(vt), len(gt))):
                say(f"| {i + 1} | {vt[i] if i < len(vt) else '-'} | {gt[i] if i < len(gt) else '-'} |")
        say("\n" + "=" * 100 + "\nStability: mean word similarity of run 1 vs runs 2-3 (1.000 = identical)\n" + "=" * 100)
        for name, vt in meta_runs.items():
            gt = [g["text"] for g in groq_runs[name] if g["ok"]]
            vs, gs = mean_similarity(vt), mean_similarity(gt)
            say(f"{name}: Voice Transcribe {vs:.3f} | Whisper {gs:.3f}" if vs is not None and gs is not None
                else f"{name}: n/a")
    else:
        say("GROQ_API_KEY not set (backend/.env or environment): Whisper comparison skipped.")

    out = prep_dir / ("asr_compare_whisper.txt" if groq_key else "asr_compare.txt")
    out.write_text("\n".join(log) + "\n")
    print(f"saved: {out}")


def main() -> None:
    global KEY
    if len(sys.argv) != 2:
        sys.exit("usage: python -m app.studio.spike <prepared-stem>   e.g. ../samples/prepared/reel1")
    stem = Path(sys.argv[1]).expanduser().resolve()
    KEY = (dotenv_values(ENV_PATH).get("META_API_KEY") or "").strip()
    if not KEY:
        sys.exit("META_API_KEY not found in backend/.env")

    all_frames = sorted(stem.parent.glob(stem.name + "_[0-9][0-9].jpg"))
    video_path = stem.parent / f"{stem.name}.720p.mp4"
    wav_path = stem.parent / f"{stem.name}.wav"
    print(f"stem={stem.name} frames={len(all_frames)} video={video_path.exists()} wav={wav_path.exists()}\n")

    # a. models
    rec = call("models", "GET /models", None, "GET", "/models", spark=False)
    ids: list[str] = []
    try:
        body = json.loads(rec.get("raw") or "{}")
        data = body.get("data", body) if isinstance(body, dict) else body
        ids = [m["id"] if isinstance(m, dict) else str(m) for m in data]
    except Exception as e:
        print(f"could not parse /models: {e}")
    print("model ids:")
    for i in ids:
        print(f"  {i}")
    spark, transcribe = pick_models(ids)
    print(f"\nselected spark={spark} transcribe={transcribe}\n")

    for version, model in spark.items():
        # b. frames
        if all_frames:
            step = max(len(all_frames) / 8, 1)
            picks = [all_frames[min(int(i * step), len(all_frames) - 1)] for i in range(min(8, len(all_frames)))]
            content = [{"type": "text", "text": FRAMES_PROMPT}] + [
                {"type": "image_url", "image_url": {"url": data_url(p, "image/jpeg")}} for p in picks]
            call("frames", f"spark {version} 8 frames", model, "POST", "/chat/completions",
                 json_body=chat(content, model))

        # c. native video, first success wins
        if video_path.exists():
            vurl = data_url(video_path, "video/mp4")
            attempts = [
                ("chat video_url", "/chat/completions", chat(
                    [{"type": "text", "text": VIDEO_PROMPT},
                     {"type": "video_url", "video_url": {"url": vurl}}], model)),
                ("chat file file_data", "/chat/completions", chat(
                    [{"type": "text", "text": VIDEO_PROMPT},
                     {"type": "file", "file": {"filename": video_path.name, "file_data": vurl}}], model)),
                ("chat image_url(video)", "/chat/completions", chat(
                    [{"type": "text", "text": VIDEO_PROMPT},
                     {"type": "image_url", "image_url": {"url": vurl}}], model)),
                ("responses input_video", "/responses", responses(
                    [{"type": "input_text", "text": VIDEO_PROMPT},
                     {"type": "input_video", "video_url": vurl}], model)),
                ("responses input_file", "/responses", responses(
                    [{"type": "input_text", "text": VIDEO_PROMPT},
                     {"type": "input_file", "filename": video_path.name, "file_data": vurl}], model)),
            ]
            for name, path, payload in attempts:
                if call("video", f"spark {version} {name}", model, "POST", path, json_body=payload)["ok"]:
                    break

        # d. audio
        if wav_path.exists():
            b64 = base64.b64encode(wav_path.read_bytes()).decode()
            content = [{"type": "text", "text": AUDIO_PROMPT},
                       {"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}}]
            call("audio", f"spark {version} input_audio", model, "POST", "/chat/completions",
                 json_body=chat(content, model))

    # e. Voice Transcribe
    if wav_path.exists():
        if not transcribe:
            results.append({"group": "transcribe", "label": "model lookup", "model": None, "endpoint": "-",
                            "ok": False, "status": None, "seconds": 0, "tokens_in": None, "tokens_out": None,
                            "cost_usd": None, "preview": "no transcribe model id found in /models",
                            "error": "no transcribe model id found in /models"})
            print("[ERR] transcribe | no transcribe model id found in /models\n")
        else:
            r = asr_transcribe(wav_path, transcribe)
            results.append({"group": "transcribe", "label": f"POST {r['url']} multipart request+audio",
                            "model": transcribe, "endpoint": r["url"], "ok": r["ok"], "status": r["status"],
                            "seconds": r["seconds"], "tokens_in": None, "tokens_out": None,
                            "cost_usd": r["cost_usd"], "preview": (r["transcript"] or r["error"] or "")[:300],
                            "error": r["error"]})
            print(f"[{'OK ' if r['ok'] else 'ERR'}] transcribe | {r['url']} | status={r['status']} | {r['seconds']}s "
                  f"| audioDurationMs={r['audio_ms']}\n      {results[-1]['preview'][:300]!r}\n")

    # save + summary
    out = stem.parent / "spike_results.json"
    out.write_text(json.dumps({"stem": stem.name, "spark_models": spark, "transcribe_model": transcribe,
                               "model_ids": ids, "results": results}, indent=2))
    print("=" * 110)
    print(f"{'status':<6} {'group':<10} {'label':<42} {'http':<5} {'secs':<7} {'in/out tok':<14} {'cost':<10}")
    for r in results:
        cost = f"${r['cost_usd']:.5f}" if r["cost_usd"] is not None else "n/a"
        toks = f"{r['tokens_in']}/{r['tokens_out']}"
        print(f"{'OK' if r['ok'] else 'ERR':<6} {r['group']:<10} {r['label'][:41]:<42} "
              f"{str(r['status']):<5} {str(r['seconds']):<7} {toks:<14} {cost:<10}")
    total = sum(r["cost_usd"] or 0 for r in results)
    print(f"\ntotal estimated Spark cost: ${total:.5f}\nsaved: {out}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--asr":
        asr_compare(Path(sys.argv[2]).expanduser().resolve())
    else:
        main()
