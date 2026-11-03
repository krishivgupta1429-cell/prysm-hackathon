"""Hackathon-built (Meta Global AI Developer Hackathon).

Pydantic models for the Studio perception report (schema_version "1.3": adds analysis; 1.2 added signals and music_id; 1.1 audio, consensus, captions).

A report has ten stages (watch, speech, subjects, audio, consensus, captions, signals, music_id, analysis, trends).
Embeddings are never stored in the report: only dims, L2 norms, status, seconds and cost; the vectors are .npy files in the run folder. Every stage carries a status
("ok" | "error" | "skipped"), an error string, seconds and cost_usd, so one failing
stage never hides the others.
"""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = "1.3"
Status = Literal["ok", "error", "skipped"]
# A stage can also be "partial": it produced usable output but a part of it failed (subjects: face track).
StageStatus = Literal["ok", "partial", "error", "skipped"]
FormatType = Literal["talking head", "dance", "skit", "voiceover", "montage", "other"]
FORMAT_TYPES = ("talking head", "dance", "skit", "voiceover", "montage", "other")


def to_seconds(value):
    """Accept 3, 3.5, "3.5", "3s" or "0:03" / "1:02:03" and return seconds as a float."""
    if isinstance(value, str):
        text = value.strip().lower().rstrip("s").strip()
        if ":" in text:
            total = 0.0
            for part in text.split(":"):
                total = total * 60 + float(part)
            return total
        return float(text)
    return value


class StageBase(BaseModel):
    status: StageStatus = "skipped"
    error: str | None = None
    seconds: float = 0.0
    cost_usd: float = 0.0


# ---------------------------------------------------------------- watch

class Hook(BaseModel):
    what_happens: str = ""
    attention_rating: int = Field(default=0, ge=0, le=10)
    rating_reason: str = ""

    @field_validator("attention_rating", mode="before")
    @classmethod
    def _clamp_rating(cls, v):
        return max(0, min(10, round(float(v))))


class Beat(BaseModel):
    start_s: float
    end_s: float
    what_happens: str = ""

    _secs = field_validator("start_s", "end_s", mode="before")(lambda cls, v: to_seconds(v))


class WeakMoment(BaseModel):
    start_s: float
    end_s: float
    problem: str = ""

    _secs = field_validator("start_s", "end_s", mode="before")(lambda cls, v: to_seconds(v))


class OnScreenText(BaseModel):
    text: str
    start_s: float = 0.0
    end_s: float = 0.0

    _secs = field_validator("start_s", "end_s", mode="before")(lambda cls, v: to_seconds(v))


class NicheGuess(BaseModel):
    niche: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_conf(cls, v):
        v = float(v)
        return max(0.0, min(1.0, v / 100 if v > 1 else v))


class TokenUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None


class WatchOutput(BaseModel):
    """What the model must return: this is the JSON schema the watch prompt asks for."""
    hook: Hook | None = None
    timeline: list[Beat] = []
    weak_moments: list[WeakMoment] = []
    on_screen_text: list[OnScreenText] = []
    setting: str = ""
    subject_description: str = ""
    format_type: FormatType = "other"
    niche_guess: NicheGuess | None = None
    cta_present: bool = False
    cta_text: str | None = None
    visual_quality_notes: str = ""

    @field_validator("format_type", mode="before")
    @classmethod
    def _normalise_format(cls, v):
        text = str(v or "").strip().lower().replace("-", " ").replace("_", " ")
        if text in FORMAT_TYPES:
            return text
        return next((f for f in FORMAT_TYPES if f != "other" and f in text), "other")


class WatchStage(StageBase, WatchOutput):
    model: str | None = None
    usage: TokenUsage | None = None
    raw_text: str | None = None  # set only when the model's JSON could not be parsed twice


# ---------------------------------------------------------------- speech

class Segment(BaseModel):
    start: float
    end: float
    text: str


class Word(BaseModel):
    start: float
    end: float
    word: str


class VoiceTranscribeResult(BaseModel):
    status: Status = "skipped"
    error: str | None = None
    transcript: str = ""
    language_guess: str = "unknown"  # "indic" (Devanagari/Gurmukhi/... script), "latin" or "unknown"
    audio_duration_ms: int | None = None
    seconds: float = 0.0
    cost_usd: float = 0.0


class WhisperResult(BaseModel):
    status: Status = "skipped"
    error: str | None = None
    language: str | None = None
    granularity: str = "none"  # "word+segment", "segment" or "none": what the API actually returned
    text: str = ""
    segments: list[Segment] = []
    words: list[Word] = []
    seconds: float = 0.0
    cost_usd: float = 0.0


class SpeechStage(StageBase):
    voice_transcribe: VoiceTranscribeResult = VoiceTranscribeResult()
    whisper: WhisperResult = WhisperResult()
    primary: Literal["voice_transcribe", "whisper"] | None = None
    primary_text: str = ""
    routing_reason: str = ""


# ---------------------------------------------------------------- subjects

class BoxPoint(BaseModel):
    t: float
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float


class Box(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class SubjectTrack(BaseModel):
    prompt: str
    detected_frames: int = 0
    object_count: int = 0
    first_seen_s: float | None = None
    screen_time_ratio: float = 0.0
    visible_in_first_3s: bool = False
    mean_confidence: float | None = None
    mean_box_area_ratio: float | None = None
    presence_per_second: list[int] = []
    box_timeline: list[BoxPoint] = []  # every 0.5 s, boxes normalised 0-1
    safe_area: Box | None = None  # union of every box over the video, normalised 0-1


class SubjectsStage(StageBase):
    person: SubjectTrack | None = None
    face: SubjectTrack | None = None
    frames_processed: int | None = None
    errors: dict[str, str] = {}  # per-track errors when one of person/face failed


# ---------------------------------------------------------------- audio listeners

def _as_list(v):
    """Models sometimes return a string or null where a list is expected."""
    if v is None or v == "":
        return []
    return [v] if isinstance(v, str) else v


def _clamp10(v):
    return None if v is None else max(0, min(10, round(float(v))))


class SparkAudioOutput(BaseModel):
    quoted_words: str = ""
    music_present: bool | None = None
    language: str | None = None
    voice_music_balance: str = ""
    quality_problems: list[str] = []

    _lists = field_validator("quality_problems", mode="before")(lambda cls, v: _as_list(v))


class SparkAudioResult(SparkAudioOutput):
    status: Status = "skipped"
    error: str | None = None
    attempts: int = 0
    model: str | None = None
    usage: TokenUsage | None = None
    seconds: float = 0.0
    cost_usd: float = 0.0
    raw_text: str | None = None  # last raw answer when every attempt failed


class MusicInfo(BaseModel):
    present: bool | None = None
    description: str = ""
    mood: str = ""
    energy_0_10: int | None = None

    _e = field_validator("energy_0_10", mode="before")(lambda cls, v: _clamp10(v))


class VoiceQuality(BaseModel):
    clarity_0_10: int | None = None
    noise: str = ""
    issues: list[str] = []

    _c = field_validator("clarity_0_10", mode="before")(lambda cls, v: _clamp10(v))
    _i = field_validator("issues", mode="before")(lambda cls, v: _as_list(v))


class AvSync(BaseModel):
    ok: bool | None = None
    notes: str = ""


class SilenceSegment(BaseModel):
    start_s: float
    end_s: float

    _secs = field_validator("start_s", "end_s", mode="before")(lambda cls, v: to_seconds(v))


class GeminiAudioOutput(BaseModel):
    """The JSON the Gemini audio prompt asks for."""
    transcript: str = ""
    language: str | None = None
    speech_ratio: float | None = None
    music: MusicInfo | None = None
    voice_quality: VoiceQuality | None = None
    av_sync: AvSync | None = None
    silence_segments: list[SilenceSegment] = []
    loudness_issues: list[str] = []
    hook_audio: str = ""

    _l = field_validator("loudness_issues", "silence_segments", mode="before")(lambda cls, v: _as_list(v))

    @field_validator("speech_ratio", mode="before")
    @classmethod
    def _ratio(cls, v):
        if v is None:
            return None
        v = float(v)
        return max(0.0, min(1.0, v / 100 if v > 1 else v))


class GeminiAudioResult(GeminiAudioOutput):
    status: Status = "skipped"
    error: str | None = None
    model_used: str | None = None
    fallback_used: bool = False  # True when gemini-2.5-pro was unavailable and flash answered
    media_sent: Literal["video", "wav"] | None = None
    attempts: int = 0
    retries: int = 0  # transient failures (503, 429, timeout) retried with backoff before this answer or giving up
    usage: TokenUsage | None = None
    thinking_tokens: int | None = None
    cost_usd: float = 0.0
    cost_is_approximate: bool = True
    seconds: float = 0.0
    raw_text: str | None = None


class AudioStage(StageBase):
    spark: SparkAudioResult = SparkAudioResult()
    gemini: GeminiAudioResult = GeminiAudioResult()


# ---------------------------------------------------------------- consensus + captions

class ConsensusSegment(BaseModel):
    start_s: float
    end_s: float
    text: str
    confidence: float  # mean over words of (voters agreeing / voters covering that word)
    timing: Literal["whisper", "approximate"]
    mode: Literal["latin", "indic"] = "latin"  # which voting mode produced this cue
    mode_reason: str = ""  # why that mode was used
    whisper_role: Literal["voter", "timing-only"] = "voter"  # in indic mode Whisper's words only give timing


class SegmentMode(BaseModel):
    """The mode decision for one Whisper segment."""
    index: int
    start_s: float
    end_s: float
    mode: Literal["latin", "indic"]
    reason: str
    supporters: list[str] = []  # voters whose Indic-script text overlaps this segment
    whisper_role: Literal["voter", "timing-only"] = "voter"


class LowConfidenceWord(BaseModel):
    word: str
    index: int
    start_s: float | None = None
    confidence: float
    votes: dict[str, str] = {}  # voter -> the word it produced at this position


class ConsensusStage(StageBase):
    consensus_text: str = ""
    segments: list[ConsensusSegment] = []
    low_confidence_words: list[LowConfidenceWord] = []
    voters_used: list[str] = []
    primary: str | None = None
    ref_voter: str | None = None  # the voter whose words/timestamps are the alignment reference
    timing_source: str | None = None  # "whisper", "approximate" or "mixed"
    segment_modes: list[SegmentMode] = []  # per Whisper segment: latin or indic, and why
    warnings: list[str] = []  # cue timing problems found by the validator
    notes: list[str] = []


class CaptionCue(BaseModel):
    index: int
    start_s: float
    end_s: float
    lines: list[str]
    text: str
    edits: list[str] = []


class CaptionsStage(StageBase):
    language: str | None = None  # None = spoken language
    translated: bool = False
    model: str | None = None
    cues: list[CaptionCue] = []
    edits: list[str] = []  # every edit the editor (or the deterministic fallback) made
    fallback_cues: int = 0
    warnings: list[str] = []  # cue timing problems found by the validator
    srt_path: str | None = None
    usage: TokenUsage | None = None
    raw_text: str | None = None


# ---------------------------------------------------------------- signals + music id

class ModelSignal(BaseModel):
    """One embedding model: status and small facts only, the vector lives in a .npy file in the run folder."""
    status: Status = "skipped"
    error: str | None = None  # the reason when skipped or failed
    dims: int | None = None
    l2_norm: float | None = None  # of the pooled vector
    seconds: float = 0.0
    cost_usd: float = 0.0  # APPROXIMATE: GPU seconds x Modal price
    source: Literal["modal", "local"] | None = None
    stand_in: str | None = None  # set when another model stood in (CLIP ViT-L/14 for LanguageBind's video tower)
    files: list[str] = []
    meta: dict[str, float | int | str | bool | None] = {}


class ModalCallInfo(BaseModel):
    ok: bool = False
    error: str | None = None
    cold: bool | None = None  # True when this call started either container (main or LanguageBind)
    cold_main: bool | None = None  # the main GPU class started a fresh container for this call
    cold_lb: bool | None = None  # the LanguageBind class started a fresh container for this call
    gpu: str | None = None
    wall_seconds: float = 0.0  # what the client waited, including any cold start
    server_seconds: float = 0.0  # time inside the container for the models
    overhead_seconds: float = 0.0  # wall - server: cold start, transfer


class DemucsResult(BaseModel):
    status: Status = "skipped"
    error: str | None = None
    voice_to_music_db: float | None = None  # vocals loudness minus everything-else loudness (RMS, dB)
    vocals_rms_db: float | None = None
    rest_rms_db: float | None = None
    stems: dict[str, str] = {}  # name -> file in the run folder
    seconds: float = 0.0
    cost_usd: float = 0.0


class AudioFeatures(BaseModel):
    status: Status = "skipped"
    error: str | None = None
    rms_curve_1hz: list[float] = []  # RMS in dBFS per second
    integrated_loudness_lufs: float | None = None
    tempo_bpm: float | None = None
    onset_density_per_s: float | None = None
    silence_ratio: float | None = None
    spectral_centroid_mean_hz: float | None = None
    seconds: float = 0.0


class SceneCuts(BaseModel):
    status: Status = "skipped"
    error: str | None = None
    cut_times_s: list[float] = []
    cuts_per_10s: float | None = None
    avg_shot_length_s: float | None = None
    seconds: float = 0.0


class SignalsStage(StageBase):
    models: dict[str, ModelSignal] = {}
    demucs: DemucsResult = DemucsResult()
    audio_features: AudioFeatures = AudioFeatures()
    cuts: SceneCuts = SceneCuts()
    modal: ModalCallInfo = ModalCallInfo()
    notes: list[str] = []


class MusicWindow(BaseModel):
    start_s: float
    end_s: float
    source: Literal["mix", "rest_stem"]
    http_status: int | None = None
    acr_code: int | None = None  # ACRCloud status code (0 = match, 1001 = no result)
    acr_msg: str | None = None
    matched: bool = False
    error: str | None = None


class MusicTrack(BaseModel):
    title: str | None = None
    artists: list[str] = []
    album: str | None = None
    label: str | None = None
    release_date: str | None = None
    acrid: str | None = None
    score: float | None = None
    play_offset_ms: int | None = None
    duration_ms: int | None = None
    external_ids: dict[str, str] = {}  # e.g. spotify, youtube, isrc


class MusicIdStage(StageBase):
    matched: bool | None = None  # False is a normal "no match", not an error
    source: Literal["mix", "rest_stem"] | None = None  # what the matching audio was
    windows: list[MusicWindow] = []
    track: MusicTrack | None = None
    heard_music: dict[str, bool | None] = {}  # what Gemini and Spark said about music
    agreement: Literal["true", "false", "unknown"] = "unknown"
    agreement_notes: str = ""


# ---------------------------------------------------------------- analysis

class ParamScore(BaseModel):
    """Compact per-parameter result kept in the report (the full detail is in analysis.json)."""
    id: str
    name: str
    scored: bool
    score: int | None = None
    confidence: str | None = None
    unsupported: bool = False
    evidence_limited: bool = False  # Trend Alignment: no live trend data in this version
    label: str | None = None  # "estimate (no live trend data)" for Trend Alignment
    min: int | None = None  # across the scoring runs
    max: int | None = None
    spread: int | None = None
    stable: bool = True
    range: list[int] | None = None  # [min, max] when not stable


class AnalysisStage(StageBase):
    niche: str | None = None
    niche_confidence: float | None = None
    niche_source: str | None = None  # "creator" (picked by the creator), "model", "override" or "fallback" (confidence below 0.5)
    niches: list[str] = []  # every niche the creator gave (the first is the primary one)
    description_fit: dict | None = None  # {verdict matches|partial|off, note, evidence ids, unsupported}
    overall_score: float | None = None  # computed in code from the niche weights, never by the model
    band: str | None = None
    parameters: list[ParamScore] = []
    unstable_parameters: list[str] = []  # --repeat: score spread over 10 points
    overall_spread: float | None = None
    runs: int = 0
    intent_count: int = 0
    flags: list[str] = []  # validator flags
    analysis_file: str | None = None


# ---------------------------------------------------------------- report

class VideoMeta(BaseModel):
    path: str
    duration_s: float
    width: int
    height: int
    fps: float


class TrendSource(BaseModel):
    title: str = ""
    url: str


class TrendsStage(BaseModel):
    """Live trend grounding (trends.py): what is trending now for the creator's niches, from Gemini Flash with Google Search.

    "unavailable" is the fail-soft result (no key, timeout, error, empty or malformed answer): the pipeline carries on
    and Trend Alignment stays an estimate. A trends result belongs to a niche set and a day, not to a video, so it is
    never stored in the perception cache.
    """
    status: Literal["ok", "unavailable", "skipped"] = "skipped"
    error: str | None = None
    seconds: float = 0.0
    cost_usd: float = 0.0
    model: str | None = None
    niches: list[str] = []
    platform: str = ""
    geo: str = "global"  # "India" only when the creator picked an India-centric niche
    cached: bool = False
    fetched_at: str | None = None
    keywords_used: list[str] = []
    formats: list[str] = []
    audio: list[str] = []
    hashtags: list[str] = []
    whats_working: dict[str, str] = {}
    sources: list[TrendSource] = []
    tokens_in: int | None = None
    tokens_out: int | None = None


class Stages(BaseModel):
    watch: WatchStage = WatchStage()
    speech: SpeechStage = SpeechStage()
    subjects: SubjectsStage = SubjectsStage()
    audio: AudioStage = AudioStage()
    consensus: ConsensusStage = ConsensusStage()
    captions: CaptionsStage = CaptionsStage()
    signals: SignalsStage = SignalsStage()
    music_id: MusicIdStage = MusicIdStage()
    analysis: AnalysisStage = AnalysisStage()
    trends: TrendsStage = TrendsStage()  # added after schema 1.3, additive: older reports load with the default


class Report(BaseModel):
    schema_version: str = SCHEMA_VERSION
    video: VideoMeta
    stages: Stages = Stages()
    total_cost_usd: float = 0.0
    total_seconds: float = 0.0

    def total_cost(self) -> float:
        s = self.stages
        return round(sum(st.cost_usd for st in (s.watch, s.speech, s.subjects, s.audio, s.consensus, s.captions, s.signals, s.music_id, s.analysis, s.trends)), 6)
