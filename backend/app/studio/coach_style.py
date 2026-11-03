"""Built during the Meta Global AI Developer Hackathon (new work).

The style addendum: coach_style.md (a de-identified style guide learned from three production results), appended to the two
scoring prompts when COACH_STYLE=1. The baseline prompt text itself is never edited; with the flag off the prompts are
byte-for-byte the baseline's.
"""
from functools import lru_cache
from pathlib import Path

STYLE_FILE = Path(__file__).with_name("coach_style.md")


@lru_cache(maxsize=1)
def style_text() -> str:
    return STYLE_FILE.read_text(encoding="utf-8").strip()


def addendum(enabled: bool | None = None) -> str:
    """"\\n\\n" + the style guide when enabled (default: settings.COACH_STYLE), else the empty string."""
    if enabled is None:
        from app.core.config import settings
        enabled = bool(settings.COACH_STYLE)
    return ("\n\n" + style_text()) if enabled else ""
