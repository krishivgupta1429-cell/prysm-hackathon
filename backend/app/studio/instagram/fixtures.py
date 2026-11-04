"""Built during the Meta Global AI Developer Hackathon (new work).

DEV-ONLY fixture source (INSTAGRAM_FIXTURE=1, refused outside localhost; see oauth.fixture_enabled). Serves 12 fake reels
with plausible insights and small videos generated on the fly, so the whole flow (import, frames, fingerprints, DNA,
personalised analysis, UI) can be tested without a real account. Everything is deterministic. Nothing here is real data:
captions start with "[FIXTURE]", the account is flagged is_fixture, and the UI shows a banner.

Built-in test conditions: reel 1 is under 48 h old (no insights yet), reel 4 has no watch-time metrics, reel 9 is a viral
outlier (about 9x the typical reach), reels 6 and 12 have no media_url (licensed-audio case: thumbnail and caption only, no watch-through).
"""
import random
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from app.studio.instagram.metrics import METRICS

W, H = 360, 640
FIXTURE_USERNAME = "fixture_creator"
FIXTURE_IG_ID = "fixture-0000"

# (archetype, hook text on screen, caption, duration, strength 0..1 (drives the fake insights))
_ARCH = {
    "talk_q":   dict(bg=(20, 20, 20),   text=True,  look="close-up of a person talking to camera, big caption text"),
    "list":     dict(bg=(235, 235, 235), text=True, look="numbered list on screen over a desk scene"),
    "broll":    dict(bg=(60, 90, 60),   text=False, look="slow b-roll landscape, no text"),
    "vlog":     dict(bg=(90, 70, 50),   text=False, look="handheld vlog walking shots, no captions"),
}
_REELS = [
    # arch, hook line, caption, dur, strength
    ("broll", "", "[FIXTURE] Sunday reset. No talking, just the vibe", 31, 0.2),
    ("talk_q", "Why do your reels flop?", "[FIXTURE] Why do your reels flop? Three reasons", 22, 0.85),
    ("list", "3 mistakes killing your reach", "[FIXTURE] 3 mistakes killing your reach", 18, 0.8),
    ("vlog", "", "[FIXTURE] day in my life, part 4", 38, 0.15),
    ("talk_q", "Stop doing this on camera", "[FIXTURE] Stop doing this on camera", 17, 0.75),
    ("broll", "", "[FIXTURE] morning light", 27, 0.25),
    ("list", "5 tools I use every day", "[FIXTURE] 5 tools I use every day", 24, 0.7),
    ("vlog", "", "[FIXTURE] weekend trip clips", 35, 0.3),
    ("talk_q", "I was wrong about hooks", "[FIXTURE] I was wrong about hooks. Here is why", 16, 0.98),
    ("list", "4 edits that double watch time", "[FIXTURE] 4 edits that double watch time", 21, 0.78),
    ("broll", "", "[FIXTURE] workspace tour no commentary", 29, 0.22),
    ("talk_q", "Nobody tells you this", "[FIXTURE] Nobody tells you this about posting", 19, 0.6),
]
N_FIXTURE = len(_REELS)


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:       # old Pillow
        return ImageFont.load_default()


def _frame(arch: str, hook: str, i: int, seed: int) -> Image.Image:
    spec = _ARCH[arch]
    rnd = random.Random(seed * 100 + i)
    bg = tuple(max(0, min(255, c + rnd.randint(-18, 18))) for c in spec["bg"])
    im = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(im)
    fg = (255, 255, 255) if sum(bg) < 380 else (10, 10, 10)
    if arch == "talk_q":       # a face-like blob + big caption
        d.ellipse((90, 150 + i * 4, 270, 380 + i * 4), fill=(200, 160, 130))
        d.rectangle((60, 380, 300, 560), fill=(40, 60, 110))
    elif arch == "list":
        for k in range(3):
            d.rectangle((40, 160 + k * 120, 320, 240 + k * 120), outline=fg, width=3)
    elif arch == "broll":
        d.polygon([(0, 420), (140 + i * 20, 300), (260, 380), (W, 330), (W, H), (0, H)], fill=(40, 70, 40))
        d.ellipse((250 - i * 10, 90, 310 - i * 10, 150), fill=(250, 220, 120))
    else:
        d.rectangle((0, 380 + i * 8, W, H), fill=(60, 50, 40))
        d.ellipse((120 + i * 15, 200, 220 + i * 15, 300), fill=(180, 150, 120))
    if spec["text"] and hook:
        words = hook.split()
        lines, cur = [], ""
        for w in words:
            if len(cur) + len(w) > 16:
                lines.append(cur); cur = w
            else:
                cur = (cur + " " + w).strip()
        lines.append(cur)
        y = 60 if i % 2 == 0 else 500
        for ln in lines:
            d.text((24, y), ln, fill=fg, font=_font(34))
            y += 42
    d.text((10, H - 22), "FIXTURE - not a real reel", fill=(150, 150, 150), font=_font(12))
    return im


def _make_video(idx: int, dest: str) -> bool:
    arch, hook, _cap, dur, _s = _REELS[idx]
    with tempfile.TemporaryDirectory(prefix="fixture_") as tmp:
        for i in range(6):
            _frame(arch, hook, i, idx).save(Path(tmp) / f"f{i}.png")
        fps = 6.0 / dur
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", f"{fps:.4f}", "-i", str(Path(tmp) / "f%d.png"),
                            "-r", "10", "-c:v", "libx264", "-pix_fmt", "yuv420p", dest],
                           capture_output=True, timeout=60)
    return r.returncode == 0 and Path(dest).exists()


def _insights(idx: int) -> dict[str, Optional[float]]:
    arch, _h, _c, dur, strength = _REELS[idx]
    rnd = random.Random(1000 + idx)
    out: dict[str, Optional[float]] = {m: None for m in METRICS}
    if idx == 0:                    # under 48 h: Instagram has not served numbers yet
        return out
    reach = 1500 * (0.4 + 2.4 * strength) * rnd.uniform(0.9, 1.1)
    if idx == 8:                    # the viral outlier
        reach = 1500 * 9
    views = reach * rnd.uniform(1.3, 1.9)
    watch_frac = 0.2 + 0.55 * strength + rnd.uniform(-0.04, 0.04)
    out.update(reach=round(reach), views=round(views),
               likes=round(views * (0.02 + 0.06 * strength)), comments=round(views * 0.004 * (0.5 + strength)),
               shares=round(views * 0.006 * (0.3 + strength)), saved=round(views * 0.01 * (0.3 + strength)),
               reels_skip_rate=round(max(0.05, 0.75 - 0.6 * strength + rnd.uniform(-0.04, 0.04)), 3))
    out["total_interactions"] = out["likes"] + out["comments"] + out["shares"] + out["saved"]
    if idx != 3:                    # reel 4: no watch-time metrics
        out["ig_reels_avg_watch_time"] = round(dur * watch_frac * 1000)
        out["ig_reels_video_view_total_time"] = round(out["ig_reels_avg_watch_time"] * views)
    return out


class FixtureSource:
    """Same interface as graph.InstagramSource."""
    is_fixture = True

    async def aclose(self):
        return None

    async def list_reels(self, n: int) -> list[dict]:
        now = datetime.now(timezone.utc)
        out = []
        for i in range(min(n, N_FIXTURE)):
            arch, hook, cap, dur, _s = _REELS[i]
            posted = now - timedelta(hours=20) if i == 0 else now - timedelta(days=3 + 5 * i)
            out.append({
                "id": f"fixture_{i + 1:03d}", "caption": cap, "media_type": "VIDEO", "media_product_type": "REELS",
                "media_url": None if i in (5, 11) else f"fixture://{i}", "thumbnail_url": f"fixture://thumb/{i}",
                "permalink": f"https://www.instagram.com/reel/FIXTURE{i + 1:03d}/", "timestamp": posted.isoformat(),
                "like_count": (_insights(i)["likes"] or 0) if i else 0, "comments_count": (_insights(i)["comments"] or 0) if i else 0,
                "_fixture_index": i,
            })
        return out

    async def count_reels(self, stop_at: int = 30) -> tuple[int, bool]:
        return N_FIXTURE, True

    async def insights(self, media_id: str) -> dict[str, Optional[float]]:
        return _insights(int(media_id.split("_")[1]) - 1)

    async def download(self, reel: dict, dest_path: str) -> bool:
        if not reel.get("media_url"):
            return False
        return _make_video(int(reel["_fixture_index"]), dest_path)

    async def thumbnail(self, reel: dict) -> Optional[bytes]:
        import io
        i = int(reel["_fixture_index"])
        buf = io.BytesIO()
        _frame(_REELS[i][0], _REELS[i][1], 0, i).save(buf, "JPEG", quality=70)
        return buf.getvalue()
