"""Built during the Meta Global AI Developer Hackathon (new work).

Sound effects, ALL synthesised by FFmpeg from oscillators, noise and envelopes: no downloads, no samples, no copyrighted meme audio.
Each effect is an `aevalsrc` / `anoisesrc` chain that produces a mono 48 kHz stream; `NOMINAL_LUFS` is the integrated loudness each
renders at before any gain (measured once, see tests), so the mixer can set an effect an exact number of dB under the voice.
MOMENT_EFFECTS maps what a moment means to the effects that suit it.
"""

SR = 48000
PI2 = "2*PI"

# effect -> (filter chain producing the stream, length in seconds)
def _ae(expr: str, d: float) -> str:
    return f"aevalsrc='{expr}':d={d}:s={SR}:c=mono"


EFFECTS: dict[str, tuple[str, float]] = {
    # curiosity / reveal: a bright metallic ring with shimmer; a riser leading into it
    "ring": (_ae(f"(sin({PI2}*1760*t)+0.6*sin({PI2}*2349*t)+0.45*sin({PI2}*3136*t)+0.3*sin({PI2}*4186*t))*exp(-3.2*t)*0.35", 1.4), 1.4),
    "shimmer": (_ae(f"(sin({PI2}*2637*t)*(0.6+0.4*sin({PI2}*9*t))+0.5*sin({PI2}*3520*t)*(0.6+0.4*sin({PI2}*7*t)))*exp(-2.2*t)*0.3", 1.6), 1.6),
    "riser": (_ae(f"sin({PI2}*(250*t+520*t*t))*pow(t/1.1,1.6)*0.5", 1.1), 1.1),
    # shock / hook: a deep impact, a glitch stutter
    "impact": (_ae(f"(sin({PI2}*(52+60*exp(-9*t))*t)*exp(-4.5*t)*0.6+(random(0)*2-1)*exp(-30*t)*0.25)", 0.9), 0.9),
    "glitch": (_ae(f"(if(lt(mod(t,0.07),0.035),1,0.15))*(sin({PI2}*(700+900*floor(t*14)*0.37)*t)*0.4+(random(0)*2-1)*0.25)*(1-t/0.42)", 0.42), 0.42),
    # funny fail / awkward
    "scratch": (_ae(f"(sin({PI2}*(1100*t-2300*t*t))*0.5+(random(0)*2-1)*0.3)*(1-t/0.4)", 0.4), 0.4),
    "womp": (_ae(f"(sin({PI2}*(if(lt(t,0.42),233,if(lt(t,0.86),220,185))*t)+2.2*sin({PI2}*5.5*t))*0.5+0.25*sin({PI2}*2*(if(lt(t,0.42),233,if(lt(t,0.86),220,185))*t)))*(if(lt(t,0.42),1,if(lt(t,0.86),0.9,1))*exp(-0.6*mod(t,0.44)))*(1-pow(t/1.5,3))", 1.5), 1.5),
    "boing": (_ae(f"sin({PI2}*(240*t+45*sin({PI2}*13*t)/13*{PI2}/{PI2}))*exp(-3.3*t)*0.7", 0.7), 0.7),
    "toot": (_ae(f"(sin({PI2}*340*t)+0.5*sin({PI2}*680*t)+0.3*sin({PI2}*1020*t))*if(lt(mod(t,0.24),0.15),1,0)*exp(-1.2*t)*0.4", 0.5), 0.5),
    # cuts and emphasis
    "whoosh": ("anoisesrc=d=0.45:c=pink:r=48000:a=0.7,highpass=f=500,lowpass=f=7000,afade=t=in:st=0:d=0.3,afade=t=out:st=0.3:d=0.15", 0.45),
    "pop": (_ae(f"sin({PI2}*(1500*t-5000*t*t))*exp(-26*t)*0.8", 0.14), 0.14),
    "ding": (_ae(f"(sin({PI2}*2093*t)*exp(-5.5*t)+0.35*sin({PI2}*4186*t)*exp(-8*t))*0.6", 0.9), 0.9),
}

# loudness (LUFS, integrated, as rendered) of each effect at gain 0 dB; filled by `calibrate` (run in the tests, values pasted here)
NOMINAL_LUFS: dict[str, float] = {"ring": -17.2, "shimmer": -21.3, "riser": -15.3, "impact": -20.7, "glitch": -15.8, "scratch": -12.4, "womp": -12.2,
                                  "boing": -12.0, "toot": -14.5, "whoosh": -25.3, "pop": -26.0, "ding": -15.4}

# moment kind -> effects that suit it, best first
MOMENT_EFFECTS = {
    "hook": ["impact", "glitch"],
    "curiosity": ["ring", "shimmer", "riser"],
    "shock": ["impact", "glitch"],
    "funny": ["scratch", "womp", "boing", "toot"],
    "transition": ["whoosh"],
    "emphasis": ["pop", "ding"],
}
MOMENTS = tuple(MOMENT_EFFECTS)
# how far under the voice an effect sits, by intensity (dB)
DEPTH_DB = {"subtle": 24.0, "balanced": 22.0, "punchy": 20.0}
# at most this many effects (zoom punches + sound effects) in any 10 s window, by intensity
PER_10S = {"subtle": 2, "balanced": 3, "punchy": 5}
MIN_EFFECT_GAP_S = 1.5
MIN_SOUND_GAP_S = 3.0


def effect_for(kind: str, requested: str | None = None) -> str:
    """The effect to use: the requested one if it exists and suits the moment, else the best for the moment."""
    options = MOMENT_EFFECTS.get(kind, MOMENT_EFFECTS["emphasis"])
    if requested in EFFECTS and requested in options:
        return requested
    return options[0]


def lines(index: int, effect: str, at: float, gain_db: float) -> tuple[str, str]:
    """(filter line, label) for one effect placed `at` seconds into the output, `gain_db` relative to its nominal level."""
    chain, _d = EFFECTS[effect]
    ms = max(int(at * 1000), 0)
    label = f"[hx{index}]"
    return (f"{chain},volume={gain_db:.1f}dB,aformat=sample_fmts=fltp:channel_layouts=stereo,adelay={ms}|{ms}{label}", label)


def gain_db(effect: str, voice_lufs: float | None, intensity: str) -> float:
    """dB to apply so the effect lands DEPTH_DB under the voice. `voice_lufs` = the loudness of the speech before the final loudness stage."""
    voice = voice_lufs if voice_lufs is not None else -20.0
    return round(voice - DEPTH_DB.get(intensity, 22.0) - NOMINAL_LUFS.get(effect, -24.0), 1)
