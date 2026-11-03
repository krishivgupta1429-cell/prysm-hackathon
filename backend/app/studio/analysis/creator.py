"""Built during the Meta Global AI Developer Hackathon (new work).

Creator-provided inputs for the analysis: the niche list (1 to 3 niches, which the website joins with
commas) and the description (which the website sends as `brief`). The model no longer guesses the niche on
this path: the first niche the creator picked is the primary one, the others reach the scoring prompt as
context only.

WEIGHTS follow the pre-hackathon baseline's own lookup: the primary niche, lower-cased, is looked up in the
calibrated profiles (comedy, dance, fitness, bollywood / indiancreator); everything else uses the default
weights. The baseline's numbers live in app/core/config.py, which only adapter.py may import, so the adapter
passes them in as `weights_override`. Without an override (the CLI) the calibrated profiles in niches.py are
used for those niches and flat 1/6 weights for the rest.

NICHE NOTES and the profile mapping are hand-written heuristics, not data-derived.
"""
import dataclasses
import re
from dataclasses import dataclass, field

from app.studio.analysis.niches import (BASELINE_LABEL, FLAT_LABEL, FLAT_WEIGHTS, NICHES, PARAM_NAMES, SCORED_IDS, Niche)

MAX_NICHES = 3
MAX_DESCRIPTION = 300
HOST_WEIGHTS_LABEL = "weights supplied by the host app (the baseline lookup: calibrated profile or default weights)"

# The niches the website offers, in its order.
WEBSITE_NICHES = ["Comedy", "Fitness", "Education", "Lifestyle", "Food", "Tech", "Fashion", "Travel", "Finance", "Business",
                  "Motivation", "Gaming", "Beauty", "Music", "Sports", "Health & Wellness", "Dance", "DIY & Crafts",
                  "Photography", "Entertainment", "Marketing", "Vlogs", "Self Improvement", "Memes", "News & Politics", "Other"]

# Studio niche ids (and the baseline's own keys) that may also be typed, mapped to a display name.
ALIASES = {"talking_head_education": "Education", "fashion_beauty": "Fashion", "music_performance": "Music",
           "bollywood_indian_creator": "Bollywood", "comedy": "Comedy", "dance": "Dance", "fitness": "Fitness", "food": "Food",
           "travel": "Travel", "other": "Other", "bollywood": "Bollywood", "indiancreator": "IndianCreator"}

# Baseline keys that have a calibrated weight profile -> our copy of that profile (see niches.py)
CALIBRATED = {"comedy": "comedy", "dance": "dance", "fitness": "fitness", "bollywood": "bollywood_indian_creator",
              "indiancreator": "bollywood_indian_creator"}

# Which Studio profile supplies the per-parameter judging notes for each website niche (heuristic).
PROFILE_FOR = {"Comedy": "comedy", "Fitness": "fitness", "Education": "talking_head_education", "Lifestyle": "other",
               "Food": "food", "Tech": "talking_head_education", "Fashion": "fashion_beauty", "Travel": "travel",
               "Finance": "talking_head_education", "Business": "talking_head_education", "Motivation": "talking_head_education",
               "Gaming": "other", "Beauty": "fashion_beauty", "Music": "music_performance", "Sports": "fitness",
               "Health & Wellness": "fitness", "Dance": "dance", "DIY & Crafts": "other", "Photography": "travel",
               "Entertainment": "comedy", "Marketing": "talking_head_education", "Vlogs": "other",
               "Self Improvement": "talking_head_education", "Memes": "comedy", "News & Politics": "talking_head_education",
               "Other": "other", "Bollywood": "bollywood_indian_creator", "IndianCreator": "bollywood_indian_creator"}

# One short judging note per niche (hand-written heuristics, not data-derived).
NICHE_NOTES = {
    "Comedy": "Timing and relatability decide it: the premise should land in the first seconds and the punchline should stand alone.",
    "Fitness": "Viewers want visible form and a clear takeaway: show the movement, the count and the result early.",
    "Education": "Lead with the surprising claim or promised takeaway, then deliver it in clear, structured beats with captions.",
    "Lifestyle": "Authenticity and a consistent look matter: a relatable moment, good light and a clear personal angle.",
    "Food": "Show the finished dish early and make the steps easy to follow; appetite appeal and clarity beat polish.",
    "Tech": "Credibility and clarity: demonstrate the product or idea fast, show proof and avoid jargon.",
    "Fashion": "The look is the hook: show the outfit early, in good light, with clear styling or product details.",
    "Travel": "Sell the place in the first seconds with a striking view, then add specific details viewers can save.",
    "Finance": "Trust is the currency: a concrete claim, simple numbers on screen and no unverifiable promises.",
    "Business": "Lead with the result or a contrarian insight, then give a concrete, credible takeaway.",
    "Motivation": "A specific, strong opening line and an emotional arc; generic quotes without a personal angle fall flat.",
    "Gaming": "Open on the best moment, keep commentary energetic and the action readable on a small vertical screen.",
    "Beauty": "Show the transformation or product result clearly and close up, with accurate colour and light.",
    "Music": "The sound is the product: clean audio, an immediate hook line and the performer clearly visible.",
    "Sports": "Open on the peak moment, keep the action in frame and add context so non-fans can follow.",
    "Health & Wellness": "Credible, practical and safe: a clear takeaway, calm delivery and no exaggerated medical claims.",
    "Dance": "A beat-synced hook, full body in frame and moves worth copying; the track carries the video.",
    "DIY & Crafts": "Process clarity: show the finished piece first, then tight steps with visible progress and materials.",
    "Photography": "Image quality is the content: strong composition, light and a distinct style, ideally with a technique shown.",
    "Entertainment": "A fast, surprising hook and a strong emotional or comedic payoff; look for shareable moments.",
    "Marketing": "Show a concrete tactic with proof or numbers; hook with the outcome and keep it actionable.",
    "Vlogs": "A story with a hook: curiosity early, varied shots and a payoff the viewer waits for.",
    "Self Improvement": "A specific promise and one actionable idea; vague advice loses viewers, a personal angle helps.",
    "Memes": "Format recognition and timing: the reference must be instantly legible and the payoff fast.",
    "News & Politics": "Accuracy and clarity first: attribute claims, avoid sensationalism and state the key fact early.",
    "Other": "Judge it against the creator's own description and what the format needs.",
    "Bollywood": "A recognisable Hindi or Punjabi track, a lip-sync or outfit hook and beat-matched cuts carry this style.",
    "IndianCreator": "A recognisable Indian-language track, a styled look and beat-matched cuts carry this style.",
}


def clean_text(value: str, limit: int) -> str:
    """Strip control characters and collapse whitespace (the baseline strips control characters from the same inputs)."""
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", value or "")).strip()[:limit]


def canonical_name(raw: str) -> str | None:
    """The website's spelling of a niche (case-insensitive, Studio ids and baseline keys accepted), or None if unknown."""
    key = clean_text(raw, 60).lower()
    for name in WEBSITE_NICHES:
        if name.lower() == key:
            return name
    return ALIASES.get(key.replace(" ", "_")) or ALIASES.get(key)


def parse_niches(value: str | list[str] | None, strict: bool = False) -> list[str]:
    """1 to 3 niches from the website's comma-joined string (or a list), de-duplicated, in the creator's order.

    Known names are returned in their canonical spelling, anything else as typed (the baseline accepts any
    text). With strict=True an unknown name raises ValueError (used by the CLI).
    """
    parts = value if isinstance(value, list) else re.split(r"[,;]", value or "")
    out: list[str] = []
    for raw in parts:
        text = clean_text(raw, 60)
        if not text:
            continue
        name = canonical_name(text)
        if name is None and strict:
            raise ValueError(f"unknown niche {text!r}; choose from: {', '.join(WEBSITE_NICHES)}")
        name = name or text
        if name.lower() not in {n.lower() for n in out}:
            out.append(name)
    return out[:MAX_NICHES]


@dataclass
class CreatorContext:
    """What the creator told us. Unverified: it is context for judging fit, never evidence for a score."""
    niches: list[str] = field(default_factory=list)
    description: str = ""

    @property
    def primary(self) -> str:
        return self.niches[0] if self.niches else "Other"

    @property
    def extras(self) -> list[str]:
        return self.niches[1:]

    @classmethod
    def from_inputs(cls, niche: str | list[str] | None, description: str | None = "", strict: bool = False) -> "CreatorContext":
        return cls(parse_niches(niche, strict), clean_text(description or "", MAX_DESCRIPTION))

    def summary(self) -> str:
        names = ", ".join(self.niches) or "not given"
        return f"niches: {names} (primary: {self.primary}); description: {self.description or 'not given'}"


def weights_for(primary: str, weights_override: dict[str, float] | None = None) -> tuple[dict[str, float], str]:
    """Weights per scored parameter id for the primary niche, by the baseline's lookup rule."""
    if weights_override is not None:
        return {pid: float(weights_override.get(pid, 0.0)) for pid in SCORED_IDS}, HOST_WEIGHTS_LABEL
    key = CALIBRATED.get(primary.strip().lower())
    if key:
        return dict(NICHES[key].weights), BASELINE_LABEL
    return dict(FLAT_WEIGHTS), FLAT_LABEL


def weights_by_id(weights_by_name: dict[str, float]) -> dict[str, float]:
    """Convert a {"Hook & Opening": 0.19, ...} mapping (the baseline's keys) to {"hook": 0.19, ...}."""
    by_name = {name: pid for pid, name in PARAM_NAMES.items()}
    return {by_name[n]: float(w) for n, w in weights_by_name.items() if n in by_name}


def resolve_niche(creator: CreatorContext, weights_override: dict[str, float] | None = None) -> Niche:
    """The Niche to score with: the primary niche's weights, a Studio profile's per-parameter notes, and the
    primary niche's one-line note. Extra niches are not used here (they go to the prompt as context)."""
    name = creator.primary
    canonical = canonical_name(name) or "Other"
    profile = NICHES[PROFILE_FOR.get(canonical, "other")]
    weights, label = weights_for(name, weights_override)
    return dataclasses.replace(profile, id=name, description=NICHE_NOTES.get(canonical, NICHE_NOTES["Other"]),
                               weights=weights, weights_label=label)
