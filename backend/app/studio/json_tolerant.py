"""Built during the Meta Global AI Developer Hackathon (new work).

Tolerant JSON extraction for model replies: Spark (like any chat model) sometimes wraps JSON in code fences, adds a
sentence before or after it, or stops mid-way. `extract_json_object` returns the first complete top-level JSON object
in the text (string- and escape-aware); if the object never closes it returns the text from the first brace so the
caller's own repair step (json_repair, bracket closing) can finish the job. Prompts are not touched.
"""
import re

_FENCE = re.compile(r"```(?:json|JSON)?\s*\n?")


def strip_fences(text: str) -> str:
    return _FENCE.sub("", text or "").replace("```", "").strip()


def extract_json_object(text: str) -> str:
    """The first balanced {...} (or [...]) in `text`, else everything from the first opening brace, else the stripped text."""
    s = strip_fences(text)
    starts = [i for i in (s.find("{"), s.find("[")) if i >= 0]
    if not starts:
        return s
    start = min(starts)
    stack, in_str, esc = [], False, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            stack.append("}" if c == "{" else "]")
        elif c in "}]":
            if stack:
                stack.pop()
            if not stack:
                return s[start:i + 1]
    return s[start:]
