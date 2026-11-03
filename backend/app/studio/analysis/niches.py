"""Hackathon-built (Meta Global AI Developer Hackathon).

Niches, parameters, weights and judging notes for the scored analysis.

WEIGHTS. Four niches (comedy, dance, fitness, bollywood_indian_creator) carry the numeric weights of the
pre-hackathon baseline config (`NICHE_WEIGHTS` in backend/app/core/config.py, read but not imported). They are
labelled "pre-hackathon baseline weights, derived from research data that is not part of this repo". Nothing
else was taken from that file except the names of the four advisory parameters. Every other niche uses flat
weights (1/6 each). Weights apply to the 6 scored parameters only; the 4 advisory ones are shown, not scored.

JUDGING NOTES are hand-written heuristics from general creator knowledge, NOT data-derived. Each is at most
two sentences, one per (niche, parameter).
"""
from dataclasses import dataclass

BASELINE_LABEL = "pre-hackathon baseline weights, derived from research data that is not part of this repo"
FLAT_LABEL = "flat weights (1/6 each): no data-derived weights for this niche"
NOTES_LABEL = "hand-written heuristics, not data-derived"

# (id, display name, scored)
PARAMETERS = [
    ("hook", "Hook & Opening", True),
    ("retention", "Retention & Pacing", True),
    ("audio", "Audio & Music", True),
    ("visual", "Visual Quality & Originality", True),
    ("trend", "Trend Alignment", True),
    ("shareability", "Shareability", True),
    ("cta", "Call to Action", False),
    ("on_screen_text", "On-Screen Text", False),
    ("save_potential", "Save Potential", False),
    ("replay_value", "Replay Value", False),
]
SCORED_IDS = [p[0] for p in PARAMETERS if p[2]]
ADVISORY_IDS = [p[0] for p in PARAMETERS if not p[2]]
PARAM_NAMES = {p[0]: p[1] for p in PARAMETERS}

# Raw per-parameter values in SCORED_IDS order, normalised by their sum exactly as the baseline config does
# (round(value / total, 4)). bollywood_indian_creator uses the same profile the baseline gives bollywood and
# indiancreator (identical to dance).
_BASELINE_RAW = {
    "comedy": ([10.9, 9.1, 8.7, 9.3, 12.1, 11.9], 62.0),
    "dance": ([9.6, 10.1, 10.9, 11.1, 12.2, 11.4], 65.3),
    "fitness": ([10.6, 9.6, 9.8, 11.2, 11.5, 12.6], 65.3),
    "bollywood_indian_creator": ([9.6, 10.1, 10.9, 11.1, 12.2, 11.4], 65.3),
}
FLAT_WEIGHTS = {pid: round(1 / 6, 4) for pid in SCORED_IDS}

TREND_LABEL = "estimate (no live trend data)"  # shown wherever Trend Alignment is shown and no live trends were available
GROUNDED_LABEL = "grounded (live trends)"  # Trend Alignment judged against the live trend lines of the evidence pack
TREND_PREFIX = "No live trend data in this version: judge only from the identified track and format cues. "

_NOTES: dict[str, dict[str, str]] = {
    "comedy": {
        "hook": "The premise or first laugh must land within 1-2 seconds, ideally with a face, a line or an absurd image on screen. A slow greeting or lead-in is a hook failure.",
        "retention": "Comedy lives on timing: tight cuts around beats and no dead air between setup and payoff. Any gap over a few seconds before the punchline needs a reason.",
        "audio": "Dialogue must be clearly intelligible and sit above any music. Sound effects and audio punchlines should hit exactly on the cut.",
        "visual": "Framing should keep the performer's face and reactions readable; polish matters less than clarity. An original character or premise counts more than production value.",
        "trend": TREND_PREFIX + "A recognisable sound or meme format helps, but do not assume a format is trending.",
        "shareability": "Shareable comedy is relatable and quotable: would someone send it to a friend as 'this is us'? Look for a universal situation and a punchline that stands alone.",
        "cta": "Comedy rarely needs a hard CTA; a light 'follow for part 2' only helps if it does not delay the payoff.",
        "on_screen_text": "Captions should carry the jokes for sound-off viewers without spoiling the punchline early.",
        "save_potential": "Comedy is sent more than saved; low save potential is normal unless it is a reusable template or a quotable line.",
        "replay_value": "Loopable endings, fast gags and background details worth a second look raise replays.",
    },
    "dance": {
        "hook": "The first 2 seconds need an arresting pose, a clean beat drop or a transition tease with the dancer fully in frame. Standing still before the move starts costs the hook.",
        "retention": "Cuts and transitions should land on the beat and the choreography should build, not repeat. A static stretch with no new move or angle invites a swipe.",
        "audio": "The track is the video: it should be loud, clean and cut so the strongest beat meets the strongest move. Voice-over or talking over the music hurts here.",
        "visual": "Full body in frame, stable camera and readable lighting matter most, along with an original move, outfit or location. Heavy cropping that cuts off feet or hands is a defect.",
        "trend": TREND_PREFIX + "A widely used track or choreography format is a positive cue only if the identified track supports it.",
        "shareability": "Dance is shared when people want to copy or tag a friend: learnable choreography and a standout moment help.",
        "cta": "A CTA is optional for dance; a short 'try this' or tag prompt works if it does not cover the performer.",
        "on_screen_text": "Minimal text is better: a short title or credit that stays out of the dancer's body area.",
        "save_potential": "Saves come from tutorials or choreography worth learning; a clear, repeatable sequence raises this.",
        "replay_value": "A clean loop, a surprise ending move or dense choreography rewards rewatching.",
    },
    "fitness": {
        "hook": "Open with the result, the hardest rep or a bold claim within 2 seconds, with the body or movement clearly visible. Setup talk and gear checks delay the hook.",
        "retention": "Keep reps, cues and cuts moving so each beat adds a new exercise, number or form tip. Long unbroken sets without on-screen counters lose attention.",
        "audio": "Energetic music or clear coaching voice both work if the voice stays intelligible over the track. Gym noise that buries the coach is an issue.",
        "visual": "Camera angle must show full-body form, with stable framing and enough light to see technique. Original drills or visible progress beat generic footage.",
        "trend": TREND_PREFIX + "A recognisable workout format or sound helps; do not assume trend status without evidence.",
        "shareability": "People share workouts they want to do with someone or results that inspire. A specific, achievable takeaway raises sharing.",
        "cta": "A clear 'save this workout' or 'try it' CTA suits fitness and works best near the end.",
        "on_screen_text": "Exercise names, rep counts and timers on screen make the clip followable with sound off.",
        "save_potential": "Fitness is saved when it is a complete, followable routine or a clear form guide.",
        "replay_value": "Short, high-intensity clips and visible transformations get replayed.",
    },
    "bollywood_indian_creator": {
        "hook": "Indian creator reels often win on an instant song hook, a lip-sync glance, an outfit reveal or a Hindi/Punjabi line in the first 2 seconds. A slow or silent start loses viewers raised on fast cuts.",
        "retention": "Match cuts and transitions to the song's beats and lyrics, with a reveal or outfit change to carry the middle. Long static takes between beats read as dead time.",
        "audio": "A recognisable Hindi or Punjabi track, well mixed against any voice, drives the video. A cut that lands on a lyric or drop is a strong sign.",
        "visual": "Bright, flattering framing, a styled outfit and a clean background count heavily, along with creative transitions. Poor lighting on the face is a clear weakness.",
        "trend": TREND_PREFIX + "A currently popular film or regional song is a strong cue only if the identified track makes it visible.",
        "shareability": "Shares come from relatable or aspirational moments: festive looks, desi humour, trending-sound lip-syncs and fashion tags.",
        "cta": "A casual CTA ('comment for the designer', 'follow for more') is common and fits the style if short.",
        "on_screen_text": "Lyric captions or short hook text in Hindi or English help sound-off viewers; keep them off the face.",
        "save_potential": "Outfit, styling and recipe-style details get saved, so product or designer tags raise this.",
        "replay_value": "A beat-synced transition or outfit reveal that rewards a second watch raises replays.",
    },
    "talking_head_education": {
        "hook": "State the surprising claim, question or promised takeaway in the first 2 seconds, spoken to camera with the topic visible. Greetings and 'in this video' intros cost the hook.",
        "retention": "Use jump cuts, on-screen visuals and a clear structure so each beat adds a new point. Long monologues with no cut or visual change lose viewers.",
        "audio": "Clear, close voice with little noise and no competing music is the priority; background music must stay far below speech. Echo, hiss or uneven levels are major issues.",
        "visual": "Eye-level framing, readable lighting and a clean background; supporting visuals or captions raise perceived quality. A distracting background is a weakness.",
        "trend": TREND_PREFIX + "Educational content depends more on topic than sound; do not infer trend status without evidence.",
        "shareability": "Shared when it teaches something surprising or counters a common belief that viewers will forward. A single memorable takeaway helps.",
        "cta": "A clear CTA (follow for more, comment a keyword) suits educational content, ideally after the payoff.",
        "on_screen_text": "Captions are close to mandatory for this niche: accurate, large and synced, with key terms emphasised.",
        "save_potential": "Educational reels are saved when they are a checklist, framework or steps worth revisiting.",
        "replay_value": "Dense, fast explanations that reward a rewatch raise replays; very simple ones do not.",
    },
    "fashion_beauty": {
        "hook": "Open with the finished look, the reveal or a striking close-up within 2 seconds. A long preamble before the product or outfit appears loses the hook.",
        "retention": "Show clear before-and-after steps or outfit changes with cuts on movement. Static shots of the same angle for long stretches are a risk.",
        "audio": "Music should fit the mood and stay balanced with any voice-over, with clear speech if explaining products. Distorted or too-quiet voice hurts.",
        "visual": "Lighting, colour and detail shots are central: skin, fabric and make-up should be clearly visible. Original styling or a distinct aesthetic scores well.",
        "trend": TREND_PREFIX + "A popular sound or styling format is a positive cue only when the identified track supports it.",
        "shareability": "Shared when viewers want the look themselves or tag a friend: clear product, brand or styling info helps.",
        "cta": "'Comment for details' or 'link in bio' works when the viewer wants the item.",
        "on_screen_text": "Product names, brands or step labels on screen, placed clear of the face and the item.",
        "save_potential": "Looks, routines and product lists are saved; clear naming of items raises this.",
        "replay_value": "A satisfying reveal or transformation that rewards a second look raises replays.",
    },
    "food": {
        "hook": "Show the finished dish, a sizzle or a dramatic pour in the first 2 seconds. Ingredient lists and talking before any food is visible lose the hook.",
        "retention": "Quick, clear steps with close-ups and visible progress keep viewers; long waits or repeated shots do not. Every cut should show a change in the food.",
        "audio": "Natural food sounds (sizzle, crunch) or fitting music with a clear voice-over work; music must not bury instructions. Muffled voice is an issue.",
        "visual": "Overhead or close-up framing, good light and appetising colour are essential, and an original dish or technique stands out.",
        "trend": TREND_PREFIX + "A recognisable viral recipe format is a positive cue only if the evidence shows it.",
        "shareability": "Shared when the dish looks achievable and craveable ('make this for me'). Clear, simple steps raise sharing.",
        "cta": "'Save for later' or 'try this' CTAs fit; a recipe in the caption makes them credible.",
        "on_screen_text": "Ingredient names, quantities and step labels on screen make the recipe followable with sound off.",
        "save_potential": "Recipes are among the most saved reels: complete steps and quantities raise this a lot.",
        "replay_value": "Satisfying process shots and a strong final reveal raise replays.",
    },
    "travel": {
        "hook": "Open with the most striking view, a surprising place or a bold claim in the first 2 seconds. Slow establishing shots of ordinary scenery lose the hook.",
        "retention": "Vary shot size, movement and location every few seconds, with transitions that carry the viewer on. Repeating similar scenery without new information drags.",
        "audio": "Music should match the mood and rhythm of the cuts, with ambient sound or voice-over clear when used. Wind noise or uneven levels are defects.",
        "visual": "Stable, well-exposed footage with strong composition and colour is the main driver; unique locations or angles set it apart.",
        "trend": TREND_PREFIX + "A popular travel transition or audio format helps only if the evidence shows it.",
        "shareability": "Shared when viewers want to go there or send it to a travel partner; specific, useful place details help.",
        "cta": "'Save for your trip' or 'tag who you would go with' CTAs fit this niche.",
        "on_screen_text": "Location names and short tips on screen help; keep them off the key scenery.",
        "save_potential": "Itineraries, hidden-spot lists and prices are saved; specificity raises this.",
        "replay_value": "Dramatic scenery and seamless transitions reward a rewatch.",
    },
    "music_performance": {
        "hook": "Start with the strongest hook line, riff or vocal moment inside 2 seconds, with the performer visible. A long intro before the music begins loses viewers.",
        "retention": "Alternate angles or framing to follow the song's build, and let the best part arrive early enough. A single static take of a long intro is a risk.",
        "audio": "Audio quality is the product: clean, loud, well balanced vocals and instrument, no clipping or room noise. Weak sound is a major issue.",
        "visual": "The performer and instrument should be well lit and framed, with a distinctive setting or styling. Visual polish should not distract from the sound.",
        "trend": TREND_PREFIX + "Performing a popular song or an original with a recognisable hook is a cue only if evidenced.",
        "shareability": "Shared when the performance moves people or invites duets, covers and tags; a strong, singable moment helps.",
        "cta": "'Full song on my profile' or 'duet this' CTAs fit performance clips.",
        "on_screen_text": "Lyrics or song credits on screen help; keep them readable and clear of the performer.",
        "save_potential": "Saves come from songs viewers want to hear again or learn; clear credits raise this.",
        "replay_value": "A strong hook line or a climactic ending that loops well raises replays.",
    },
    "other": {
        "hook": "Judge whether the first 2 seconds show a clear subject, an intriguing action or a promise that makes a viewer stay. Slow starts, greetings and logos are weak hooks.",
        "retention": "Look for new information, visuals or beats every few seconds and a clear arc to the end. Long static or repetitive stretches are risks.",
        "audio": "Audio should be clear, balanced and suit the content, with speech intelligible over any music. Noise, clipping or silence gaps are defects.",
        "visual": "Check framing, lighting, stability and whether the look is distinctive or generic. Judge against what the format needs, not against production value alone.",
        "trend": TREND_PREFIX + "Do not assume trend status without evidence in the identified track or format.",
        "shareability": "Ask whether a viewer would send this to someone: relatable, surprising or useful content scores higher.",
        "cta": "A CTA is optional; where present it should be short, specific and not delay the payoff.",
        "on_screen_text": "Captions or text should add information for sound-off viewers and stay readable and clear of the subject.",
        "save_potential": "Content with reusable information, steps or inspiration is saved; pure entertainment is saved less.",
        "replay_value": "Loopable endings and details worth a second look raise replays.",
    },
}

_DESCRIPTIONS = {
    "comedy": "sketches, skits, jokes, funny reactions, memes",
    "dance": "choreography, dance trends, lip-sync dance performances",
    "fitness": "workouts, exercise tutorials, gym and body-transformation content",
    "bollywood_indian_creator": "Indian creators: Bollywood/Hindi/Punjabi songs, desi lip-syncs, Indian fashion and festive content",
    "talking_head_education": "a person explaining or teaching to camera: tips, explainers, commentary",
    "fashion_beauty": "outfits, styling, make-up, skincare, product looks",
    "food": "cooking, recipes, food reviews, street food",
    "travel": "destinations, vlogs, scenery, trip tips",
    "music_performance": "singing, playing instruments, original music, covers",
    "other": "anything that does not clearly fit the niches above",
}


@dataclass(frozen=True)
class Niche:
    id: str
    description: str
    weights: dict[str, float]  # over SCORED_IDS
    weights_label: str
    judging_notes: dict[str, str]  # over all 10 parameters, hand-written heuristics


def _weights(niche_id: str) -> tuple[dict[str, float], str]:
    if niche_id in _BASELINE_RAW:
        raw, total = _BASELINE_RAW[niche_id]
        return {pid: round(v / total, 4) for pid, v in zip(SCORED_IDS, raw)}, BASELINE_LABEL
    return dict(FLAT_WEIGHTS), FLAT_LABEL


NICHES: dict[str, Niche] = {}
for _id, _notes in _NOTES.items():
    _w, _label = _weights(_id)
    NICHES[_id] = Niche(_id, _DESCRIPTIONS[_id], _w, _label, _notes)

NICHE_IDS = list(NICHES)


def get_niche(niche_id: str | None) -> Niche:
    """The niche, or "other" for an unknown id."""
    return NICHES.get(niche_id or "", NICHES["other"])
