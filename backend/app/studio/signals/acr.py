"""Built during the Meta Global AI Developer Hackathon (new work). Existing models and services, new integration:
the models and vendors wrapped here existed before the hackathon; only this integration code is new.

ACRCloud audio recognition over plain httpx (no SDK).

POST https://{host}/v1/identify as multipart with access_key, data_type "audio", signature_version "1",
signature, sample_bytes, timestamp and the audio sample. ACRCloud is an existing third-party API; this
file is our integration. It only ever receives short audio clips of the creator's own reel.

The signature is base64(HMAC-SHA1(access_secret, string_to_sign)) with
string_to_sign = "POST\\n/v1/identify\\n{access_key}\\naudio\\n1\\n{timestamp}".
The secret and the signature are never logged or stored.
"""
import base64
import hashlib
import hmac
import io
import time
import wave
from pathlib import Path

import httpx

from app.studio import config
from app.studio.report import MusicTrack

HTTP_METHOD = "POST"
HTTP_URI = "/v1/identify"
DATA_TYPE = "audio"
SIGNATURE_VERSION = "1"
CODE_MATCH = 0
CODE_NO_RESULT = 1001


class AcrError(RuntimeError):
    """Transport or auth failure; the message never contains the key, secret or signature."""


def sign(access_key: str, access_secret: str, timestamp: str) -> str:
    """base64(HMAC-SHA1(secret, "POST\\n/v1/identify\\n{key}\\naudio\\n1\\n{timestamp}"))."""
    string_to_sign = "\n".join([HTTP_METHOD, HTTP_URI, access_key, DATA_TYPE, SIGNATURE_VERSION, timestamp])
    digest = hmac.new(access_secret.encode(), string_to_sign.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def wav_window(wav_path: Path, start_s: float, length_s: float) -> tuple[bytes, float, float]:
    """A PCM wav slice [start, start + length) as bytes, with the real (start, end) in seconds."""
    with wave.open(str(wav_path), "rb") as w:
        rate, width, channels, total = w.getframerate(), w.getsampwidth(), w.getnchannels(), w.getnframes()
        first = max(0, min(int(start_s * rate), total))
        count = max(0, min(int(length_s * rate), total - first))
        w.setpos(first)
        frames = w.readframes(count)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(frames)
    return buf.getvalue(), first / rate, (first + count) / rate


def window_starts(duration_s: float, length_s: float = config.ACR_WINDOW_S) -> list[float]:
    """Where to take the identify windows: none under 6 s, the start (+ the middle) up to 45 s, three over 45 s."""
    if duration_s < config.ACR_MIN_REEL_S:
        return []
    if duration_s <= length_s:
        return [0.0]
    last = duration_s - length_s
    starts = [0.0, round(last / 2, 2)]
    if duration_s > 45:
        starts.append(round(last, 2))
    return starts


def _name(item) -> str | None:
    return item.get("name") if isinstance(item, dict) else (item if isinstance(item, str) else None)


def parse_response(body: dict) -> tuple[int | None, str | None, MusicTrack | None]:
    """(ACRCloud status code, status message, best track or None) from an identify response.

    code 0 with a music entry is a match; 1001 ("No result") and anything else is a no-match or an error
    for the caller to interpret. The best entry is the highest-score one.
    """
    status = body.get("status") or {}
    code, msg = status.get("code"), status.get("msg")
    music = (body.get("metadata") or {}).get("music") or []
    if code != CODE_MATCH or not music:
        return code, msg, None
    best = max(music, key=lambda m: m.get("score") or 0)
    external = {}
    meta = best.get("external_metadata") or {}
    spotify = ((meta.get("spotify") or {}).get("track") or {}).get("id")
    youtube = (meta.get("youtube") or {}).get("vid")
    deezer = ((meta.get("deezer") or {}).get("track") or {}).get("id")
    for key, value in (("spotify", spotify), ("youtube", youtube), ("deezer", deezer)):
        if value:
            external[key] = str(value)
    for key, value in (best.get("external_ids") or {}).items():  # isrc, upc
        if value:
            external[key] = str(value)
    track = MusicTrack(
        title=best.get("title"), artists=[n for n in (_name(a) for a in best.get("artists") or []) if n],
        album=_name(best.get("album")), label=best.get("label"), release_date=best.get("release_date"),
        acrid=best.get("acrid"), score=best.get("score"), play_offset_ms=best.get("play_offset_ms"),
        duration_ms=best.get("duration_ms"), external_ids=external)
    return code, msg, track


def identify(sample: bytes, host: str, access_key: str, access_secret: str,
             retries: int = config.ACR_RETRIES, timeout: float = config.ACR_REQUEST_TIMEOUT_S):
    """One identify request (one retry on a timeout or 5xx). Returns (http_status, parsed body)."""
    last = "no attempt"
    for attempt in range(retries + 1):
        timestamp = str(int(time.time()))
        data = {"access_key": access_key, "data_type": DATA_TYPE, "signature_version": SIGNATURE_VERSION,
                "signature": sign(access_key, access_secret, timestamp), "sample_bytes": str(len(sample)),
                "timestamp": timestamp}
        try:
            r = httpx.post(f"https://{host}{HTTP_URI}", data=data, timeout=timeout,
                           files={"sample": ("sample.wav", sample, "audio/wav")})
            if r.status_code >= 500:
                last = f"HTTP {r.status_code}"
            else:
                return r.status_code, r.json()
        except (httpx.TimeoutException, ValueError) as e:
            last = type(e).__name__
        except httpx.HTTPError as e:
            raise AcrError(_scrub(f"{type(e).__name__}: {e}", host, access_key, access_secret)) from None
        if attempt < retries:
            time.sleep(1.0)
    raise AcrError(f"ACRCloud request failed after {retries + 1} attempts ({last})")


def _scrub(text: str, *secrets: str | None) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text
