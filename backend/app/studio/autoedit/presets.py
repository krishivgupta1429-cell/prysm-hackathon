"""Built during the Meta Global AI Developer Hackathon (new work).

Style presets: parameter sets for caption size / colour / position, zoom intensity and cut aggressiveness.
`resolve_style` merges a preset with per-edit overrides (from revisions) and clamps every number to a safe range.
"""

PRESETS = {
    "clean": {
        "caption_size": 78, "caption_color": "FFFFFF", "highlight_color": "FFD43B", "caption_position": "lower",
        "caption_font_weight": "bold", "zoom_scale": 1.08, "zoom_max": 3, "cut_aggressiveness": 0.5,
        "hook_size": 92, "overlay_box": False,
    },
    "energetic": {
        "caption_size": 92, "caption_color": "FFFFFF", "highlight_color": "FFE600", "caption_position": "lower",
        "caption_font_weight": "extrabold", "zoom_scale": 1.14, "zoom_max": 6, "cut_aggressiveness": 0.9,
        "hook_size": 108, "overlay_box": False,
    },
    "minimal": {
        "caption_size": 64, "caption_color": "FFFFFF", "highlight_color": "FFFFFF", "caption_position": "lower",
        "caption_font_weight": "regular", "zoom_scale": 1.08, "zoom_max": 1, "cut_aggressiveness": 0.25,
        "hook_size": 76, "overlay_box": True,
    },
}
DEFAULT_PRESET = "clean"
POSITIONS = ("upper", "middle", "lower")
LIMITS = {"caption_size": (52, 120), "hook_size": (60, 140), "zoom_scale": (1.0, 1.15), "zoom_max": (0, 8), "cut_aggressiveness": (0.0, 1.0)}


def resolve_style(preset: str | None, overrides: dict | None = None) -> dict:
    name = preset if preset in PRESETS else DEFAULT_PRESET
    style = {"preset": name, **PRESETS[name]}
    for k, v in (overrides or {}).items():
        if k in LIMITS and isinstance(v, (int, float)) and not isinstance(v, bool):
            lo, hi = LIMITS[k]
            style[k] = type(PRESETS[name][k])(min(max(v, lo), hi))
        elif k == "caption_position" and v in POSITIONS:
            style[k] = v
        elif k in ("caption_color", "highlight_color") and isinstance(v, str) and len(v) == 6 and all(c in "0123456789abcdefABCDEF" for c in v):
            style[k] = v.upper()
        elif k == "overlay_box" and isinstance(v, bool):
            style[k] = v
        elif k == "caption_font_weight" and v in ("regular", "bold", "extrabold"):
            style[k] = v
    return style
