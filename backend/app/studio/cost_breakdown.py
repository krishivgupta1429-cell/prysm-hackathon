"""Built during the Meta Global AI Developer Hackathon (new work).

A per-analysis cost breakdown, added next to the baseline's `_cost` items: Muse Spark/Wonder calls (vision, scoring, chat)
with tokens in/out INCLUDING the hidden reasoning tokens (Meta bills completion tokens, reasoning included), the Spark 1.2
audio listener, Voice Transcribe, SAM 3.1, Gemini, Modal GPU seconds (an ESTIMATE: seconds x a configurable rate),
ACRCloud and Apify as per-call flags, and the total in USD.
"""
from app.studio import config


def _llm(usage: dict | None) -> dict | None:
    if not usage:
        return None
    return {"model": usage.get("model"), "tokens_in": int(usage.get("input_tokens") or 0), "tokens_out": int(usage.get("output_tokens") or 0),
            "reasoning_tokens": int(usage.get("reasoning_tokens") or 0), "cost_usd": round(float(usage.get("cost_usd") or 0.0), 6),
            "calls": int(usage.get("calls") or 1)}


def _sum_ledger(entries, service: str, model: str | None = None) -> dict:
    rows = [e for e in entries if e.service == service and (model is None or e.detail == model)]
    return {"calls": len(rows), "tokens_in": sum(e.tokens_in for e in rows), "tokens_out": sum(e.tokens_out for e in rows),
            "reasoning_tokens": sum(e.reasoning_tokens for e in rows), "units": round(sum(e.units for e in rows), 3),
            "cost_usd": round(sum(e.usd for e in rows), 6)}


def build(*, vision_usage: dict | None, scoring_usage: dict | None, chat_usage: dict | None = None, gemini_audio_usd: float = 0.0,
          gemini_audio_tokens: tuple = (0, 0), evidence=None, apify_usd: float | None = None, source_type: str = "upload",
          acr_called: bool = False, acr_matched: bool = False, trends_called: bool = False, total_usd: float = 0.0) -> dict:
    ledger = list(getattr(evidence, "ledger", None) or [])
    out: dict = {"currency": "USD"}

    llm = {"vision": _llm(vision_usage), "scoring": _llm(scoring_usage),
           "chat": _llm(chat_usage) or {"model": None, "tokens_in": 0, "tokens_out": 0, "reasoning_tokens": 0, "cost_usd": 0.0, "calls": 0,
                                          "note": "ARIA chat is billed per message and counted in the daily spend, not in the analysis"}}
    out["llm"] = {k: v for k, v in llm.items() if v}
    out["wonder_used"] = any((v or {}).get("model") == config.WONDER_MODEL for v in llm.values()) \
        or any(e.service == "wonder" for e in ledger)

    spark12 = _sum_ledger(ledger, "spark", "muse-spark-1.2")
    out["spark_1_2_audio"] = {"model": "muse-spark-1.2", **{k: spark12[k] for k in ("calls", "tokens_in", "tokens_out", "reasoning_tokens", "cost_usd")}}
    other_spark = [e for e in ledger if e.service in ("spark", "wonder") and e.detail != "muse-spark-1.2"]
    if other_spark:
        out["other_meta_llm_in_evidence"] = {"calls": len(other_spark), "tokens_in": sum(e.tokens_in for e in other_spark),
                                             "tokens_out": sum(e.tokens_out for e in other_spark), "cost_usd": round(sum(e.usd for e in other_spark), 6)}
    voice = _sum_ledger(ledger, "voice_transcribe")
    out["voice_transcribe"] = {"calls": voice["calls"], "audio_seconds": voice["units"], "cost_usd": voice["cost_usd"]}
    sam = _sum_ledger(ledger, "sam")
    out["sam_3_1"] = {"calls": sam["calls"], "frames": int(sam["units"]), "cost_usd": sam["cost_usd"]}
    gem_ledger = _sum_ledger(ledger, "gemini")
    out["gemini"] = {
        "audio": {"tokens_in": int(gemini_audio_tokens[0]), "tokens_out": int(gemini_audio_tokens[1]), "cost_usd": round(gemini_audio_usd, 6)},
        "evidence_calls": {k: gem_ledger[k] for k in ("calls", "tokens_in", "tokens_out", "cost_usd")},
        "trends": {"called": bool(trends_called), "cost_usd": None, "note": "Gemini + Google Search; the baseline does not meter this call"},
    }
    sig = getattr(evidence, "signals", None)
    modal = getattr(sig, "modal", None)
    seconds = float(getattr(modal, "server_seconds", 0.0) or 0.0)
    out["modal_gpu"] = {"estimate": True, "gpu": getattr(modal, "gpu", None), "gpu_seconds": round(seconds, 2),
                        "rate_usd_per_s": round(float(getattr(sig, "cost_usd", 0.0) or 0.0) / seconds, 6) if seconds else None,
                        "cost_usd": round(float(getattr(sig, "cost_usd", 0.0) or 0.0), 6),
                        "note": "GPU seconds x a configurable rate (MODAL_USD_PER_GPU_SECOND); excludes up to 120 s idle scaledown"}
    out["acrcloud"] = {"called": bool(acr_called), "matched": bool(acr_matched), "cost_usd": None, "note": "per-call fee, not metered here"}
    out["apify"] = {"called": source_type == "link", "cost_usd": None if apify_usd is None else round(apify_usd, 6)}
    out["total_usd"] = round(float(total_usd), 6)
    return out


def summary_line(b: dict) -> str:
    """One line for the backend log: who cost what."""
    def llm(k):
        v = (b.get("llm") or {}).get(k) or {}
        return f"{k}={v.get('model') or '-'} {v.get('tokens_in', 0)}in/{v.get('tokens_out', 0)}out({v.get('reasoning_tokens', 0)}reasoning) ${v.get('cost_usd', 0):.4f}"
    s12 = b.get("spark_1_2_audio") or {}
    return ("[JobCost] total=$%.4f | %s | %s | spark1.2-audio $%.4f | voice %.0fs $%.4f | sam %d frames $%.4f | modal ~%.0f GPU-s ~$%.4f (estimate) | gemini-audio $%.4f"
            " | acr=%s apify=%s" % (b.get("total_usd", 0), llm("vision"), llm("scoring"), s12.get("cost_usd", 0),
                                    (b.get("voice_transcribe") or {}).get("audio_seconds", 0), (b.get("voice_transcribe") or {}).get("cost_usd", 0),
                                    (b.get("sam_3_1") or {}).get("frames", 0), (b.get("sam_3_1") or {}).get("cost_usd", 0),
                                    (b.get("modal_gpu") or {}).get("gpu_seconds", 0), (b.get("modal_gpu") or {}).get("cost_usd", 0),
                                    ((b.get("gemini") or {}).get("audio") or {}).get("cost_usd", 0),
                                    "called" if (b.get("acrcloud") or {}).get("called") else "no", "called" if (b.get("apify") or {}).get("called") else "no"))
