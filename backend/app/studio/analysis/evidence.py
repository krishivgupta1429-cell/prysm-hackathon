"""Built during the Meta Global AI Developer Hackathon (new work).

Evidence pack: turns a report into a compact list of facts for the analysis model, hard-capped at about
6,000 tokens (config.EVIDENCE_MAX_TOKENS). Facts only: no embeddings, no model opinions (hook ratings,
weak moments and niche guesses from the watch stage are left out on purpose).

Every item gets an id (E1, E2, ...) and a timestamp or range when it has one, so a score can cite exactly
what it rests on. If the pack is too big it is shrunk in steps (fewer cut times, shorter transcript,
fewer beats) and the pack says what was shortened.
"""
import math
from dataclasses import dataclass, field

from app.studio import config
from app.studio.analysis.creator import CreatorContext
from app.studio.report import Report

# (cut times, consensus chars, beats, on-screen items, low-confidence words, silences) kept at each shrink level
LEVELS = [(40, 2500, 20, 40, 15, 12), (20, 1200, 12, 25, 10, 8), (10, 600, 8, 15, 6, 5), (5, 300, 5, 8, 3, 3)]


def estimate_tokens(text: str) -> int:
    """Rough token count: ~4 characters per token for ASCII, ~1.5 per character for other scripts."""
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return math.ceil(ascii_chars / 4 + (len(text) - ascii_chars) / 1.5)


@dataclass
class EvidenceItem:
    id: str
    source: str
    text: str
    t_start: float | None = None
    t_end: float | None = None

    def when(self) -> str:
        if self.t_start is None:
            return ""
        if self.t_end is None or abs(self.t_end - self.t_start) < 0.05:
            return f"[{self.t_start:g}s]"
        return f"[{self.t_start:g}-{self.t_end:g}s]"

    def line(self) -> str:
        return f"{self.id} {self.when()} {self.source}: {self.text}".replace("  ", " ")


@dataclass
class EvidencePack:
    items: list[EvidenceItem] = field(default_factory=list)
    shortened: list[str] = field(default_factory=list)  # what had to be cut to fit the cap
    audio_listener_missing: bool = False  # the Gemini audio facts are not in the pack (validate.py caps Audio & Music)
    creator_item_id: str | None = None  # the creator-context item: shown to the model, never valid evidence for a score
    trends_ok: bool = False  # the pack holds live trend lines (source "trends"): Trend Alignment can be grounded

    def ids(self) -> set[str]:
        return {i.id for i in self.items}

    def citable_ids(self) -> set[str]:
        """Evidence ids a score may cite: everything except the creator's own (unverified) claims."""
        return self.ids() - ({self.creator_item_id} if self.creator_item_id else set())

    def trend_ids(self) -> set[str]:
        return {i.id for i in self.items if i.source == "trends"}

    def by_id(self) -> dict[str, EvidenceItem]:
        return {i.id: i for i in self.items}

    def render(self) -> str:
        return "\n".join(i.line() for i in self.items)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.render())

    def as_dict(self) -> dict:
        return {"tokens": self.tokens, "shortened": self.shortened, "audio_listener_missing": self.audio_listener_missing,
            "creator_item_id": self.creator_item_id, "trends_ok": self.trends_ok,
                "items": [{"id": i.id, "source": i.source, "t_start": i.t_start, "t_end": i.t_end, "text": i.text}
                          for i in self.items]}


def _clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _fmt(x, digits: int = 2) -> str:
    return "n/a" if x is None else (f"{x:.{digits}f}".rstrip("0").rstrip(".") if isinstance(x, float) else str(x))


def _build(report: Report, level: int, creator: CreatorContext | None = None) -> tuple[list[EvidenceItem], list[str]]:
    cuts_n, consensus_n, beats_n, text_n, lowconf_n, silence_n = LEVELS[level]
    s = report.stages
    items: list[EvidenceItem] = []
    shortened: list[str] = []

    def add(source: str, text: str, t0: float | None = None, t1: float | None = None):
        if text and text.strip():
            items.append(EvidenceItem(f"E{len(items) + 1}", source, text.strip(), t0, t1))

    v = report.video
    add("video", f"duration {v.duration_s:g}s, {v.width}x{v.height}, {v.fps:g} fps")
    if creator is not None and (creator.niches or creator.description):
        add("creator context", f"CREATOR-PROVIDED, UNVERIFIED (not evidence about the video): {_clip(creator.summary(), 420)}")

    # ---- watch: facts about what is on screen (no ratings, weak moments or niche guesses)
    w = s.watch
    if w.status == "ok":
        if w.hook:
            add("watch", f"first 3 seconds on screen: {_clip(w.hook.what_happens, 300)}", 0, 3)
        scene = "; ".join(x for x in (f"setting: {_clip(w.setting, 140)}" if w.setting else "",
                                      f"subject: {_clip(w.subject_description, 180)}" if w.subject_description else "",
                                      f"format: {w.format_type}") if x)
        add("watch", scene)
        for b in w.timeline[:beats_n]:
            add("watch", f"scene: {_clip(b.what_happens, 160)}", b.start_s, b.end_s)
        if len(w.timeline) > beats_n:
            shortened.append(f"scene beats cut to {beats_n} of {len(w.timeline)}")
        for t in w.on_screen_text[:text_n]:
            add("watch", f'on-screen text: "{_clip(t.text, 80)}"', t.start_s, t.end_s)
        if len(w.on_screen_text) > text_n:
            shortened.append(f"on-screen text cut to {text_n} of {len(w.on_screen_text)}")
        add("watch", f"call to action: {w.cta_text if w.cta_present else 'none'}")
        add("watch", f"visual quality notes: {_clip(w.visual_quality_notes, 220)}")

    # ---- speech: consensus transcript
    c, sp = s.consensus, s.speech
    if c.status == "ok":
        early = " ".join(q.text for q in c.segments if q.start_s < 3)
        add("speech", f"spoken or sung in the first 3 seconds: {_clip(early, 160) or '(nothing)'}", 0, 3)
        lang = sp.whisper.language or sp.voice_transcribe.language_guess
        add("speech", f"language: {lang}; primary listener: {sp.primary}; consensus transcript: "
                      f"{_clip(c.consensus_text, consensus_n)}")
        if len(c.consensus_text) > consensus_n:
            shortened.append(f"transcript cut to {consensus_n} characters")
        if c.low_confidence_words:
            words = ", ".join(f"{x.word}@{x.start_s:g}s" if x.start_s is not None else x.word
                              for x in c.low_confidence_words[:lowconf_n])
            add("speech", f"low-confidence words ({len(c.low_confidence_words)} total): {words}")

    # ---- audio listeners
    a = s.audio
    g = a.gemini
    if g.status == "ok":
        if g.music:
            add("audio/gemini", f"music present: {_fmt(g.music.present)}; {_clip(g.music.description, 140)}; "
                                f"mood {_clip(g.music.mood, 40)}; energy {_fmt(g.music.energy_0_10)}/10; "
                                f"speech ratio {_fmt(g.speech_ratio)}")
        if g.voice_quality:
            add("audio/gemini", f"voice clarity {_fmt(g.voice_quality.clarity_0_10)}/10; noise: "
                                f"{_clip(g.voice_quality.noise, 100)}; issues: {_clip('; '.join(g.voice_quality.issues), 160) or 'none'}")
        if g.av_sync:
            add("audio/gemini", f"A/V sync ok: {_fmt(g.av_sync.ok)}; {_clip(g.av_sync.notes, 120)}")
        if g.silence_segments:
            sil = ", ".join(f"{x.start_s:g}-{x.end_s:g}s" for x in g.silence_segments[:silence_n])
            add("audio/gemini", f"silent stretches: {sil}")
        if g.loudness_issues:
            add("audio/gemini", f"loudness problems: {_clip('; '.join(g.loudness_issues), 200)}")
        add("audio/gemini", f"audio in the first 3 seconds: {_clip(g.hook_audio, 220)}", 0, 3)
    if g.status != "ok":  # say so explicitly: absence of audio facts is not evidence that the audio is fine
        why = "not run" if g.status == "skipped" else _clip(g.error or "no answer", 100)
        add("audio/gemini", f"audio facts UNAVAILABLE: the Gemini audio listener produced no result ({why}); "
                            "music, voice quality, A/V sync, silences and loudness were not analysed by Gemini")
    sk = a.spark
    if sk.status == "ok":
        add("audio/spark", f"music present: {_fmt(sk.music_present)}; voice vs music: {_clip(sk.voice_music_balance, 140)}; "
                           f"problems: {_clip('; '.join(sk.quality_problems), 160) or 'none'}")

    # ---- subjects
    for name, tr in (("person", s.subjects.person), ("face", s.subjects.face)):
        if tr:
            safe = f"{tr.safe_area.x1:.2f},{tr.safe_area.y1:.2f}-{tr.safe_area.x2:.2f},{tr.safe_area.y2:.2f}" if tr.safe_area else "n/a"
            add("subjects", f"{name}: first seen {_fmt(tr.first_seen_s)}s; visible in first 3 s: {tr.visible_in_first_3s}; "
                            f"on screen {tr.screen_time_ratio:.0%} of the video; safe area (x1,y1-x2,y2, 0-1) {safe}")

    # ---- signals (computed measurements)
    sg = s.signals
    if sg.cuts.status == "ok":
        cut_times = sg.cuts.cut_times_s
        shown = ", ".join(f"{t:g}" for t in cut_times[:cuts_n])
        if len(cut_times) > cuts_n:
            shortened.append(f"cut times cut to {cuts_n} of {len(cut_times)}")
        add("signals", f"cuts per 10 s: {_fmt(sg.cuts.cuts_per_10s)}; average shot length {_fmt(sg.cuts.avg_shot_length_s)}s; "
                       f"{len(cut_times)} cuts at (s): {shown or 'none'}")
    af = sg.audio_features
    if af.status == "ok":
        add("signals", f"tempo {_fmt(af.tempo_bpm)} bpm; integrated loudness {_fmt(af.integrated_loudness_lufs)} LUFS; "
                       f"silence ratio {_fmt(af.silence_ratio)}")
    if sg.demucs.status == "ok":
        add("signals", f"voice-to-music loudness ratio {_fmt(sg.demucs.voice_to_music_db)} dB (positive = voice louder)")
    enc = sg.models.get("encodec")
    if enc and enc.status == "ok" and enc.meta.get("codebook_usage_ratio") is not None:
        add("signals", f"EnCodec codebook usage {enc.meta['codebook_usage_ratio']} (higher = more complex audio)")

    # ---- music id
    m = s.music_id
    if m.status == "ok":
        if m.matched and m.track:
            add("music_id", f"identified track: {m.track.title} by {', '.join(m.track.artists)}; matched on {m.source}; "
                            f"play offset {_fmt((m.track.play_offset_ms or 0) / 1000)}s")
        else:
            add("music_id", "no track identified")

    # ---- live trends (from a Google search made today for the creator's niches; not facts about this video)
    t = s.trends
    if t.status == "ok":
        where = f" (India context)" if t.geo == "India" else ""
        head = f"LIVE TREND from a Google search today{where}, for {', '.join(t.niches)} on {t.platform or 'short-form video'}"
        if t.formats:
            add("trends", f"{head}: trending formats: " + "; ".join(_clip(x, 90) for x in t.formats[:6]))
        if t.audio:
            add("trends", f"{head}: trending audio: " + "; ".join(_clip(x, 90) for x in t.audio[:6]))
        if t.hashtags:
            add("trends", f"{head}: trending hashtags: " + " ".join(t.hashtags[:8]))
        for niche, line in t.whats_working.items():
            add("trends", f"{head}: what is working now in {niche}: {_clip(line, 200)}")
    return items, shortened


def build_evidence_pack(report: Report, creator: CreatorContext | None = None) -> EvidencePack:
    """The evidence pack for a report, shrunk until it fits config.EVIDENCE_MAX_TOKENS.

    With a CreatorContext the pack also holds one "creator context" item (niches and description, labelled
    creator-provided and unverified). It is context for judging fit; validate.py never accepts it as evidence.
    """
    pack = EvidencePack()
    for level in range(len(LEVELS)):
        items, shortened = _build(report, level, creator)
        creator_id = next((i.id for i in items if i.source == "creator context"), None)
        pack = EvidencePack(items, shortened, audio_listener_missing=report.stages.audio.gemini.status != "ok",
                            creator_item_id=creator_id, trends_ok=any(i.source == "trends" for i in items))
        if pack.tokens <= config.EVIDENCE_MAX_TOKENS:
            return pack
    while pack.items and pack.tokens > config.EVIDENCE_MAX_TOKENS:  # last resort: drop the last items (live trend lines last of all)
        droppable = [k for k, i in enumerate(pack.items) if i.source != "trends"]
        pack.items.pop(droppable[-1] if droppable else -1)
        if "dropped trailing items to fit the cap" not in pack.shortened:
            pack.shortened.append("dropped trailing items to fit the cap")
    return pack
