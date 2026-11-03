"""Hackathon-built (Meta Global AI Developer Hackathon).

Offline test that the product is called "Prysm" everywhere a user, judge or reader sees it: no "Prysm" (any case, words
separated by whitespace) in backend string literals (messages, prompts, email templates; docstrings and
comments are internal and skipped), in the messages the safety net and the adapter really return, in the website's
source (comments stripped), index.html and public/, or in the public docs. Internal identifiers such as the hyphenated
Modal app name "prysm-studio-signals" and the logger name "prysm.studio" are not user-facing and do not match.
"""
import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
BRAND = re.compile(r"prysm\s+studio", re.IGNORECASE)
SKIP_DIRS = {"__pycache__", "node_modules", "tests", ".venv"}


def backend_files():
    return [p for p in (ROOT / "backend" / "app").rglob("*.py") if not SKIP_DIRS & set(p.parts)]


def docstring_nodes(tree):
    nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                nodes.add(id(body[0].value))
    return nodes


def string_literals(path):
    tree = ast.parse(path.read_text())
    skip = docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            yield node.lineno, node.value


def test_no_backend_string_literal_says_prysm_studio():
    hits = [f"{p.relative_to(ROOT)}:{line}" for p in backend_files() for line, text in string_literals(p) if BRAND.search(text)]
    assert hits == []


BARE_STUDIO = re.compile(r"(?<![\w\-./:])studio(?![\w\-./:])", re.IGNORECASE)  # the word, not prysm-studio, app.studio, STUDIO_X
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}


def logging_literals(tree):
    """ids of string constants that are arguments of logger calls: logs are internal, not shown to users."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LOG_METHODS:
            ids.update(id(n) for arg in node.args for n in ast.walk(arg) if isinstance(n, ast.Constant))
    return ids


def test_no_prompt_note_or_message_uses_the_bare_word_studio():
    """Catches leaks like "no live trend data in Studio v1" (a model-facing note the model repeated on the results page)."""
    hits = []
    for path in backend_files():
        tree = ast.parse(path.read_text())
        skip = docstring_nodes(tree) | logging_literals(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
                if node.value.strip().lower() in {"studio", "overall_studio"}:  # config values that select the engine
                    continue
                if BARE_STUDIO.search(node.value):
                    hits.append(f"{path.relative_to(ROOT)}:{node.lineno}: {node.value[:60]!r}")
    assert hits == []


def test_the_scoring_prompt_and_judging_notes_do_not_mention_studio():
    from app.studio.analysis import niches, score
    from app.studio.analysis.creator import NICHE_NOTES
    texts = [score.PROMPT, niches.TREND_PREFIX, *NICHE_NOTES.values()]
    texts += [note for niche in niches.NICHES.values() for note in niche.judging_notes.values()]
    assert not [t for t in texts if "studio" in t.lower()]
    assert "Prysm" in score.PROMPT


def test_the_messages_users_actually_get_say_prysm():
    from app.studio import safety
    messages = [safety.DAILY_BUDGET_MESSAGE, safety.ALREADY_RUNNING_MESSAGE, safety.HOURLY_LIMIT_MESSAGE, safety.REGISTER_LIMIT_MESSAGE]
    assert safety.DAILY_BUDGET_MESSAGE == "Prysm's demo budget for today is used up. Please try again tomorrow."
    assert not [m for m in messages if "studio" in m.lower()]


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)        # /* ... */ and {/* ... */}
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)       # HTML comments
    return re.sub(r"(?<![:\w])//[^\n]*", "", text)                 # // line comments (not the // in a URL)


def web_files():
    files = [p for p in (ROOT / "web" / "src").rglob("*") if p.is_file() and p.suffix in {".ts", ".tsx", ".css", ".html", ".json"}]
    files += [ROOT / "web" / "index.html"]
    files += [p for p in (ROOT / "web" / "public").rglob("*") if p.is_file() and p.suffix in {".html", ".json", ".txt", ".webmanifest", ".xml", ""}]
    return [p for p in files if "node_modules" not in p.parts]


def test_no_website_text_says_prysm_studio():
    hits = [str(p.relative_to(ROOT)) for p in web_files() if BRAND.search(strip_comments(p.read_text(errors="ignore")))]
    assert hits == []


WEB_BARE_STUDIO = re.compile(r"(?<![\w\-./:])studio(?![\w\-./:?])", re.IGNORECASE)  # also not a property name like `studio?:`


def test_no_website_text_uses_the_bare_word_studio_except_the_existing_caption_studio_feature():
    hits = []
    for p in web_files():
        text = strip_comments(p.read_text(errors="ignore")).replace("Caption Studio", "").replace("caption-studio", "")
        if WEB_BARE_STUDIO.search(text):
            hits.append(str(p.relative_to(ROOT)))
    assert hits == []


def test_the_wordmark_and_page_title_are_plain_prysm_in_demo_mode_too():
    config = (ROOT / "web" / "src" / "lib" / "config.ts").read_text()
    assert "BRAND_NAME" not in config  # no mode-dependent brand name
    for name in ("Navbar.tsx", "Footer.tsx"):
        text = (ROOT / "web" / "src" / "components" / name).read_text()
        assert 'text-foreground">Prysm</span>' in text and "Studio" not in strip_comments(text)
    title = re.search(r"<title>(.*?)</title>", (ROOT / "web" / "index.html").read_text(), re.DOTALL).group(1)
    assert title.startswith("Prysm") and "studio" not in title.lower()
    og = re.findall(r'<meta[^>]+property="og:[^"]+"[^>]*content="([^"]*)"', (ROOT / "web" / "index.html").read_text())
    assert og and not [c for c in og if "studio" in c.lower()]


@pytest.mark.parametrize("doc", ["README.md", "DEPLOY.md", "HACKATHON.md", "backend/.env.example", "web/.env.example"])
def test_the_public_docs_say_prysm(doc):
    text = (ROOT / doc).read_text()
    assert not BRAND.search(text)
