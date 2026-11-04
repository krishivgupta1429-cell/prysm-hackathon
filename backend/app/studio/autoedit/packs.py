"""Built during the Meta Global AI Developer Hackathon (new work).

Style packs: a headline font + a caption font + an accent colour + a caption animation, as one choice. Spark picks the pack for the niche,
tone and language (creative.py); the creator can change the pack, either font, the intensity or the sound-effects switch with one click.
Every font is a Google Fonts family (open licence); `fonts.resolve` downloads it on first use and falls back to Poppins with a note.
"""

PACKS = {
    "bold_hype": {"label": "Bold Hype", "headline": "Anton", "caption": "Montserrat", "caption_weight": 800, "accent": "FFE600", "animation": "pop",
                  "for": "high-energy, challenge, motivation, reveals"},
    "clean": {"label": "Clean", "headline": "Poppins", "caption": "Poppins", "caption_weight": 700, "accent": "FFD43B", "animation": "fade",
              "for": "explainers, business, neutral"},
    "playful": {"label": "Playful", "headline": "Baloo 2", "caption": "Nunito", "caption_weight": 800, "accent": "FF6FB5", "animation": "bounce",
                "for": "lifestyle, kids, food, fun"},
    "editorial": {"label": "Editorial", "headline": "Playfair Display", "caption": "Inter", "caption_weight": 600, "accent": "E8C28A", "animation": "fade",
                  "for": "fashion, beauty, luxury, storytelling"},
    "fitness": {"label": "Fitness", "headline": "Bebas Neue", "caption": "Oswald", "caption_weight": 700, "accent": "39FF14", "animation": "slide",
                "for": "fitness, sports, gaming, hard-hitting"},
    "comedy": {"label": "Comedy", "headline": "Bangers", "caption": "Comic Neue", "caption_weight": 700, "accent": "00E5FF", "animation": "pop",
               "for": "comedy, memes, skits, reactions"},
    "hinglish": {"label": "Hindi / Hinglish", "headline": "Baloo 2", "caption": "Hind", "caption_weight": 700, "accent": "FFD43B", "animation": "pop",
                 "for": "Hindi and Hinglish speech (Devanagari-capable fonts)"},
}
ANIMATIONS = ("pop", "bounce", "fade", "slide", "none")
DEFAULT_PACK = "clean"
INTENSITIES = ("subtle", "balanced", "punchy")


def default_pack(language: str | None, niche: str | None) -> tuple[str, str]:
    """(pack, reason) without a model: used when the Spark call fails and as the starting point."""
    lang = (language or "").lower()
    n = (niche or "").lower()
    if lang in ("hindi", "hinglish", "urdu", "marathi", "punjabi", "bengali", "gujarati"):
        return "hinglish", f"the speech is {language}, so a pack with Devanagari-capable fonts"
    for key, pack in (("fit", "fitness"), ("gym", "fitness"), ("sport", "fitness"), ("gaming", "fitness"), ("comed", "comedy"), ("meme", "comedy"),
                      ("fashion", "editorial"), ("beauty", "editorial"), ("luxury", "editorial"), ("food", "playful"), ("kid", "playful"), ("lifestyle", "playful"),
                      ("motiv", "bold_hype"), ("business", "clean"), ("educat", "clean"), ("tech", "clean")):
        if key in n:
            return pack, f"fits the {niche} niche"
    return DEFAULT_PACK, "a neutral default"


def pack_style(name: str) -> dict:
    p = PACKS.get(name) or PACKS[DEFAULT_PACK]
    return {"pack": name if name in PACKS else DEFAULT_PACK, "font_headline": p["headline"], "font_caption": p["caption"],
            "caption_weight": p["caption_weight"], "highlight_color": p["accent"], "accent": p["accent"], "animation": p["animation"]}
