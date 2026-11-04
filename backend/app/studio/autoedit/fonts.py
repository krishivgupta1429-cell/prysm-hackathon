"""Built during the Meta Global AI Developer Hackathon (new work).

Google Fonts for captions and headlines.
  * `search` over a bundled snapshot of the catalogue (categories: display, sans, serif, rounded, handwriting, mono, devanagari).
  * `FontCache.get` downloads ONE STATIC TTF per (family, weight, script) from fonts.gstatic.com on first use. Asking the CSS endpoint with
    a plain client returns static instances (never a variable font), already cut by script, so libass never sees an `fvar` table.
  * Glyph coverage is checked against the real text (Latin, Devanagari). A family that cannot draw the text is replaced by a bundled
    fallback (Poppins, which has both scripts) and the reason is returned as a note the creator sees.
  * A Hindi word is drawn with the family's Devanagari file and a Latin word with its Latin file (`\\fn` per word in the ASS), because the
    Google subsets are split by script.
`fetch` (url, headers -> bytes) is injectable so everything is testable offline.
"""
import json
import re
import urllib.parse
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.studio import config as studio_config
from app.studio.autoedit import config

CATALOG = config.FONTS_DIR / "catalog.json"
CATEGORIES = ("display", "sans", "serif", "rounded", "handwriting", "mono", "devanagari")
FALLBACK = {"family": "Poppins", "name": "Poppins", "regular": "Poppins-Regular.ttf", "bold": "Poppins-Bold.ttf", "extrabold": "Poppins-ExtraBold.ttf"}
CSS_URL = "https://fonts.googleapis.com/css2?family={fam}:wght@{w}&display=swap"
UA = {"User-Agent": "curl/8"}   # a plain client gets static TTF instances, split by script


def cache_dir() -> Path:
    d = studio_config.get_env("AUTO_EDIT_FONT_DIR")
    p = Path(d).expanduser() if d else studio_config.BACKEND_DIR / "data" / "fonts"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ------------------------------------------------------------------ catalogue

@lru_cache(maxsize=1)
def _catalog() -> list[dict]:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def families() -> dict[str, dict]:
    return {f["f"]: f for f in _catalog()}


def search(q: str = "", category: str | None = None, script: str | None = None, limit: int = 60) -> list[dict]:
    """Families matching a text and/or a category / script, most popular first. -> [{family, category, scripts, weights, rounded, devanagari}]"""
    q = (q or "").strip().lower()
    out = []
    for f in _catalog():
        if q and q not in f["f"].lower():
            continue
        if category == "rounded" and not f["r"]:
            continue
        if category == "devanagari" and "devanagari" not in f["s"]:
            continue
        if category in ("display", "sans", "serif", "handwriting", "mono") and f["c"] != category:
            continue
        if script and script not in f["s"]:
            continue
        out.append({"family": f["f"], "category": "rounded" if (f["r"] and f["c"] == "sans") else f["c"], "scripts": f["s"], "weights": f["w"],
                    "rounded": f["r"], "devanagari": "devanagari" in f["s"]})
        if len(out) >= limit:
            break
    return out


def refresh_catalog(fetch) -> int:
    """Rewrite the bundled snapshot from Google's public metadata (run by hand now and then). Returns the number of families."""
    raw = fetch("https://fonts.google.com/metadata/fonts", {}).decode("utf-8")
    meta = json.loads(raw[raw.index("{"):])
    cat = {"Sans Serif": "sans", "Display": "display", "Handwriting": "handwriting", "Serif": "serif", "Monospace": "mono"}
    out = []
    for f in meta["familyMetadataList"]:
        if f.get("isBrandFont") and not f.get("isNoto"):
            continue
        out.append({"f": f["family"], "c": cat.get(f["category"], "sans"), "s": [s for s in f["subsets"] if s != "menu"],
                    "w": sorted({int(k) for k in f["fonts"] if k.isdigit()}), "p": f.get("popularity", 9999), "r": "round" in f["family"].lower()})
    out.sort(key=lambda x: x["p"])
    CATALOG.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    _catalog.cache_clear()
    return len(out)


# ------------------------------------------------------------------ script of a text

def scripts_in(text: str) -> set[str]:
    s = set()
    for ch in text:
        o = ord(ch)
        if 0x0900 <= o <= 0x097F:
            s.add("devanagari")
        elif ch.isalpha():
            s.add("latin")
    return s


def is_devanagari(word: str) -> bool:
    return any(0x0900 <= ord(c) <= 0x097F for c in word)


# ------------------------------------------------------------------ one downloaded font

@dataclass
class FontFile:
    path: Path
    name: str            # the family name inside the file (nameID 1): what the ASS must say
    bold: bool
    family: str          # what was asked for
    weight: int
    script: str
    cmap: set = field(default_factory=set, repr=False)

    def covers(self, text: str) -> bool:
        return all((ord(c) in self.cmap) for c in text if not c.isspace() and not c.isdigit() and c not in ".,!?'\"-:;()%&@#/")


def read_font(path: Path, family: str, weight: int, script: str) -> FontFile:
    from fontTools.ttLib import TTFont
    f = TTFont(str(path), lazy=True)
    if "fvar" in f:
        raise ValueError("variable font")
    nm = f["name"]
    name = (nm.getDebugName(1) or family).strip()
    sub = (nm.getDebugName(2) or "").lower()
    return FontFile(path, name, "bold" in sub, family, weight, script, set(f.getBestCmap() or {}))


def bundled(weight: int = 700) -> FontFile:
    key = "extrabold" if weight >= 800 else "bold" if weight >= 600 else "regular"
    return read_font(config.FONTS_DIR / FALLBACK[key], "Poppins", weight, "latin+devanagari")


def _blocks(css: str) -> list[tuple[str, str]]:
    """[(subset, url)] from a Google Fonts CSS reply (`/* latin */ @font-face {... src: url(...) ...}`)."""
    out = []
    for m in re.finditer(r"/\*\s*([\w-]+)\s*\*/\s*@font-face\s*\{(.*?)\}", css, re.S):
        u = re.search(r"url\((https://[^)]+)\)", m.group(2))
        if u:
            out.append((m.group(1), u.group(1)))
    if not out:
        u = re.search(r"url\((https://[^)]+)\)", css)
        if u:
            out.append(("latin", u.group(1)))
    return out


def nearest_weight(avail: list[int], want: int) -> int:
    return min(avail, key=lambda w: abs(w - want)) if avail else 400


class FontCache:
    def __init__(self, fetch=None, directory: Path | None = None):
        self.fetch = fetch or _http_get
        self.dir = Path(directory) if directory else cache_dir()
        self.dir.mkdir(parents=True, exist_ok=True)

    def get(self, family: str, weight: int, script: str = "latin") -> FontFile:
        """The static TTF of `family` at (about) `weight` for `script` (latin | devanagari). Raises when it cannot be had."""
        meta = families().get(family)
        if meta is None:
            raise ValueError(f"{family!r} is not a Google Fonts family")
        if script not in meta["s"]:
            raise ValueError(f"{family} has no {script} glyphs")
        w = nearest_weight(meta["w"], weight)
        slug = re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-")
        path = self.dir / f"{slug}-{w}-{script}.ttf"
        if not path.exists():
            css = self.fetch(CSS_URL.format(fam=urllib.parse.quote_plus(family), w=w), UA).decode("utf-8", "replace")
            blocks = _blocks(css)
            url = next((u for s, u in blocks if s == script), None) or (blocks[0][1] if len(blocks) == 1 else None)
            if url is None:
                raise ValueError(f"no {script} file offered for {family}")
            data = self.fetch(url, {})
            if len(data) < 1000 or data[:4] not in (b"\x00\x01\x00\x00", b"true", b"OTTO"):
                raise ValueError(f"{family}: the download was not a TrueType font")
            tmp = path.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(path)
        return read_font(path, family, w, script)


def _http_get(url: str, headers: dict) -> bytes:
    import httpx
    r = httpx.get(url, headers=headers, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r.content


# ------------------------------------------------------------------ a font for a real text

@dataclass
class Resolved:
    """What the renderer needs for one role (headline or caption): the Latin file and the Devanagari file, plus notes for the creator."""
    family: str
    latin: FontFile
    devanagari: FontFile | None
    notes: list[str] = field(default_factory=list)

    def spec(self) -> dict:
        d = {"family": self.family, "name": self.latin.name, "bold": self.latin.bold, "files": [str(self.latin.path)]}
        if self.devanagari:
            d.update({"dv_name": self.devanagari.name, "dv_bold": self.devanagari.bold})
            d["files"].append(str(self.devanagari.path))
        return d


def resolve(cache: FontCache, family: str, weight: int, text: str) -> Resolved:
    """Pick the files for `text`. Anything that cannot be had or cannot draw the text falls back to Poppins, with a note saying so."""
    scripts = scripts_in(text) or {"latin"}
    notes: list[str] = []
    fb = bundled(weight)

    def fetch(script):
        try:
            f = cache.get(family, weight, script)
            if not f.covers("".join(c for c in text if (script == "devanagari") == (0x0900 <= ord(c) <= 0x097F))):
                notes.append(f"{family} cannot draw some of the {script} characters in this video; Poppins is used for them.")
                return None
            return f
        except Exception as e:
            notes.append(f"{family} could not be used for {script} text ({str(e)[:80]}); Poppins is used instead.")
            return None

    latin = fetch("latin")   # always: an English hook or label on a Hindi video should still be in the pack's own font
    dv = fetch("devanagari") if "devanagari" in scripts else None
    if latin is None:
        latin = fb
    if "devanagari" in scripts and dv is None:
        dv = fb
    if "devanagari" not in scripts:
        dv = None
    used_fallback = [x for x in (latin, dv) if x is fb]
    fam = "Poppins" if (used_fallback and latin is fb) else family
    return Resolved(fam, latin, dv, list(dict.fromkeys(notes)))


def prepare_fontsdir(files: list[str], dest: Path) -> Path:
    """One directory with the bundled fonts and the downloaded ones the render uses (libass takes a single fontsdir)."""
    dest.mkdir(parents=True, exist_ok=True)
    srcs = [config.FONTS_DIR / x for x in ("Poppins-Regular.ttf", "Poppins-Bold.ttf", "Poppins-ExtraBold.ttf")] + [Path(f) for f in files]
    for s in srcs:
        t = dest / s.name
        if not t.exists():
            try:
                t.symlink_to(s)
            except OSError:
                t.write_bytes(s.read_bytes())
    return dest
