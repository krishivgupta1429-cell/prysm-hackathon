"""
region_tagger.py — proxy-based region detection for niche-research reels.

Adds two metadata fields per reel:
  region        : "india" | "international" | "unknown"
  region_source : "proxy"  (all entries tagged here; distinguishes from more
                            accurate future signals)

Detection uses only signals available from scraped metadata:
  1. Devanagari script in caption or display name  → india (very high confidence)
  2. Indian-specific hashtags / location words     → india
  3. Hinglish transliteration words in caption     → india (≥2 matches required)
  4. Clear Western-location hashtags               → international
  5. No strong signal either way                   → unknown

This module deliberately does NOT touch scores, weights, or aggregations.
"""
from __future__ import annotations

import re
from typing import Dict, Any, Tuple

# ── Script patterns ───────────────────────────────────────────────────────────
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")   # Hindi, Marathi, Nepali, Sanskrit …
_ARABIC     = re.compile(r"[؀-ۿ]")   # Urdu (Pakistan/Bangladesh signal too)
_BENGALI    = re.compile(r"[ঀ-৿]")
_GUJARATI   = re.compile(r"[઀-૿]")
_GURMUKHI   = re.compile(r"[਀-੿]")   # Punjabi
_TAMIL      = re.compile(r"[஀-௿]")
_TELUGU     = re.compile(r"[ఀ-౿]")
_KANNADA    = re.compile(r"[ಀ-೿]")
_MALAYALAM  = re.compile(r"[ഀ-ൿ]")

_SOUTH_ASIAN_SCRIPT = re.compile(
    r"[ऀ-ॿ"   # Devanagari
    r"ঀ-৿"    # Bengali
    r"਀-੿"    # Gurmukhi
    r"઀-૿"    # Gujarati
    r"଀-୿"    # Oriya
    r"஀-௿"    # Tamil
    r"ఀ-౿"    # Telugu
    r"ಀ-೿"    # Kannada
    r"ഀ-ൿ"    # Malayalam
    r"؀-ۿ"    # Arabic/Urdu
    r"]"
)

# ── India signals ─────────────────────────────────────────────────────────────

# Hashtags that strongly imply Indian origin
_INDIA_HASHTAGS: frozenset[str] = frozenset({
    # Country / nation
    "india", "indian", "bharat", "bharatmata", "incredibleindia",
    "madeinindia", "makeinindia", "vocalforlocal", "atmanirbharbharat",
    # Desi identity
    "desi", "desivibes", "desicontent", "desicreator", "desigirl",
    "desiboy", "desimeme", "desihumour", "desicouple", "indiancreator",
    "indianyoutuber", "indianreels", "browngirl", "brownboy",
    # Cities / states
    "mumbai", "delhi", "newdelhi", "bangalore", "bengaluru", "hyderabad",
    "pune", "kolkata", "chennai", "ahmedabad", "surat", "jaipur",
    "lucknow", "chandigarh", "goa", "noida", "gurgaon", "gurugram",
    "bhopal", "nashik", "rajasthan", "kerala", "gujarat", "maharashtra",
    "karnataka", "tamilnadu", "telangana", "andhra", "odisha", "punjab",
    "haryana", "uttarakhand", "himachal", "assam", "manipur", "tripura",
    "meghalaya", "nagaland", "mizoram", "arunachalpradesh", "sikkim",
    "guwahati", "kochi", "coimbatore", "bhubaneswar", "patna", "ranchi",
    "vadodara", "agra", "varanasi", "amritsar", "jodhpur", "udaipur",
    "shimla", "manali", "leh", "ladakh", "rishikesh", "haridwar",
    # Entertainment / culture
    "bollywood", "tollywood", "kollywood", "mollywood", "hindi",
    "urdu", "punjabi", "bhojpuri", "marathi", "gujarati", "bangla",
    "hindifilm", "bollywoodactor", "bollywooddance",
    "iplT20", "ipl", "bcci", "rcb", "csk", "mi", "kkr", "srh", "dc",
    "pkh", "gt", "lsg", "virat", "rohitsharma", "msdhoni", "hardik",
    "indiancricket",
    # Indian brands / platforms
    "myntra", "nykaa", "swiggy", "zomato", "flipkart", "meesho",
    "phonepe", "paytm", "jio", "airtel", "tatacliq", "snapdeal",
    "moj", "sharechat", "josh", "kuku", "mxplayer",
    "amul", "parle", "dabur", "himalaya", "patanjali",
    # Festivals
    "diwali", "holi", "navratri", "ganeshfestival", "durga",
    "eid", "onam", "pongal", "ugadi", "baisakhi", "raksha",
    "dussehra", "janmashtami", "karwachauth",
})

# Words that are near-exclusively Hinglish / Hindi-in-Latin
# Require ≥ 2 to fire (avoids false positives on borrowed words)
_HINGLISH_WORDS: frozenset[str] = frozenset({
    "hai", "hain", "hoon", "tha", "thi", "the",
    "kya", "kaise", "kyun", "kyunki", "kyuki",
    "nahi", "nhi", "nahin", "mat",
    "mujhe", "mujhko", "mera", "meri", "mere", "humara",
    "tum", "tumhara", "aap", "aapko", "apna",
    "yaar", "bhai", "dost", "bhen", "didi", "mama", "chacha",
    "toh", "toh", "aur", "lekin", "par", "magar",
    "bahut", "thoda", "zyada", "bilkul", "ekdam",
    "acha", "achha", "theek", "sahi", "galat",
    "karo", "karna", "karke", "hogaya", "huaa",
    "dekho", "dekha", "sunno", "bolo", "jao", "aao",
    "wala", "wali", "wale",
    "zindagi", "pyaar", "ishq", "dil", "mohabbat", "yaar",
    "pagal", "bewakoof", "bakwaas",
    "abhi", "phir", "pehle", "baad", "aaj", "kal",
    "ghar", "desh", "shahar", "gaon",
    "rupees", "rupiya", "lakh", "crore", "paisa",
    "chai", "roti", "daal", "biryani", "paneer",
})

# Caption text patterns for Indian context (case-insensitive)
_INDIA_TEXT_RE = re.compile(
    r"\b(?:india|indian|bharat|bollywood|mumbai|delhi|bangalore|hyderabad"
    r"|kolkata|chennai|pune|desi|hindi|rupees?|rs\.|inr)\b",
    re.IGNORECASE,
)

# ── International signals ─────────────────────────────────────────────────────

_INTL_HASHTAGS: frozenset[str] = frozenset({
    # Anglophone West
    "usa", "us", "unitedstates", "america", "american",
    "uk", "britain", "british", "england", "english",
    "canada", "canadian", "australia", "australian", "newzealand",
    "ireland", "scotland", "wales",
    # European
    "europe", "european", "germany", "german", "france", "french",
    "spain", "spanish", "italy", "italian", "netherlands", "dutch",
    "sweden", "swedish", "norway", "denmark", "finland",
    "switzerland", "austria", "belgium", "poland",
    # East / SE Asia
    "korea", "korean", "japan", "japanese", "china", "chinese",
    "thailand", "thai", "indonesia", "philippines", "vietnam",
    "malaysia", "singapore",
    # Middle East (non-South-Asian)
    "uae", "dubai", "abudhabi", "saudiarabia", "qatar", "kuwait",
    "bahrain", "oman",
    # Cities
    "london", "nyc", "newyork", "losangeles", "la", "chicago",
    "toronto", "vancouver", "sydney", "melbourne", "paris",
    "berlin", "amsterdam", "tokyo", "seoul", "beijing",
    "hongkong", "taipei",
})

_INTL_TEXT_RE = re.compile(
    r"\b(?:new\s*york|los\s*angeles|san\s*francisco|london|paris|berlin"
    r"|toronto|sydney|melbourne|seoul|tokyo|dubai|singapore"
    r"|australia|australian|canada|canadian|america|american"
    r"|england|british|france|french|germany|german|spain|spanish"
    r"|italy|italian|netherlands|sweden|norway|denmark|finland"
    r"|switzerland|ireland|scotland|new\s*zealand"
    r"|korea|korean|japan|japanese|thailand|thai|philippines"
    r"|vietnam|indonesia|malaysia"
    r"|united\s*states|united\s*kingdom)\b",
    re.IGNORECASE,
)


# ── Main tagger ───────────────────────────────────────────────────────────────

def tag_region(metadata: Dict[str, Any]) -> Tuple[str, str]:
    """
    Given a metadata dict (as stored in per_video JSON entries),
    return (region, region_source).

    region        : "india" | "international" | "unknown"
    region_source : always "proxy"
    """
    caption      = metadata.get("caption") or ""
    hashtags_raw = metadata.get("hashtags") or []
    display_name = metadata.get("uploader") or ""

    hashtags = [h.lower().lstrip("#") for h in hashtags_raw]
    combined_text = f"{caption} {display_name}"

    # ── Signal 1: South-Asian script anywhere ────────────────────────────────
    if _SOUTH_ASIAN_SCRIPT.search(combined_text):
        return "india", "proxy"

    caption_lower = caption.lower()
    name_lower    = display_name.lower()

    # ── Signal 2: India-specific hashtag ────────────────────────────────────
    if any(h in _INDIA_HASHTAGS for h in hashtags):
        return "india", "proxy"

    # ── Signal 3: India keyword in caption text ──────────────────────────────
    if _INDIA_TEXT_RE.search(caption):
        return "india", "proxy"

    # ── Signal 4: Hinglish words in caption (≥2 distinct matches) ───────────
    caption_words = set(re.split(r"[\s\W]+", caption_lower))
    hinglish_hits = caption_words & _HINGLISH_WORDS
    if len(hinglish_hits) >= 2:
        return "india", "proxy"

    # ── Signal 5: International hashtag ────────────────────────────────────
    if any(h in _INTL_HASHTAGS for h in hashtags):
        return "international", "proxy"

    # ── Signal 6: International city/country in caption ─────────────────────
    if _INTL_TEXT_RE.search(caption):
        return "international", "proxy"

    return "unknown", "proxy"


def tag_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add region / region_source fields to a per_video JSON record in-place.
    Idempotent — safe to call on already-tagged records.
    """
    meta = record.get("metadata") or {}
    region, source = tag_region(meta)
    record["region"] = region
    record["region_source"] = source
    return record
