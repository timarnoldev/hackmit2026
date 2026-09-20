"""Static checks on the pitch deck in marketing/deck.

The deck has no build step and no JavaScript test runner, so these are the cheap invariants that
have actually broken on us: a dark theme coming back (the deck is light only, because a stale
?theme=dark link or a synced peer window used to drag a live presentation into dark mode), the
brand's meaning-carrying accents going missing, and results.js drifting out of valid JSON.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

DECK = Path(__file__).resolve().parents[1] / "marketing" / "deck"

CSS = DECK / "deck.css"
JS = DECK / "deck.js"
HTML = DECK / "index.html"

# the dark surface set that used to live in :root, plus the presenter's hardcoded dark values
DARK_TOKENS = ["#060F0A", "#0B2116", "#10331F", "#EAFFF3", "#6FCB98", "#1B4A2E", "#071510", "#0C1712"]

# BRAND.md section 9: these carry meaning and are not a theme, so they must stay
BRAND_ACCENTS = {
    "--sa-audit": "#10A6C4",
    "--sa-cost": "#C41477",
    "--sa-gain": "#0E9C55",
    "--sa-tbd-fill": "#FFE13D",
}


def _payload(text: str) -> str:
    """The object after the assignment. The header comment quotes the same line, so anchor on
    the real statement at the start of a line."""
    marker = "\nwindow.RESULTS ="
    body = text[text.index(marker) + len(marker) :].strip()
    return body.rstrip(";").strip()


@pytest.fixture(scope="module")
def css() -> str:
    return CSS.read_text()


def test_no_theme_switching(css: str) -> None:
    """Light is the only theme. No selector may depend on one, in any file."""
    for path in (CSS, JS, HTML):
        text = path.read_text()
        # a comment may mention the word; a selector, attribute or query parameter may not
        offenders = [
            ln.strip()
            for ln in text.splitlines()
            if re.search(r"data-theme|setTheme|sa-theme|theme=|\btheme:", ln)
        ]
        assert not offenders, f"{path.name} still switches themes: {offenders[:3]}"


def test_no_dark_tokens(css: str) -> None:
    """If a dark surface colour reappears, something was pasted back in from the old palette."""
    found = [t for t in DARK_TOKENS if t.lower() in css.lower()]
    assert not found, f"dark palette values are back in deck.css: {found}"


def test_brand_accents_survive(css: str) -> None:
    """Removing dark mode must not have taken the four brand accents with it."""
    for token, value in BRAND_ACCENTS.items():
        assert re.search(rf"{token}:\s*{value};", css, re.I), f"{token} is not {value} any more"


def test_single_root_palette(css: str) -> None:
    assert "color-scheme: light;" in css
    assert "color-scheme: dark" not in css
    assert "prefers-color-scheme" not in css


def test_results_js_is_valid_json() -> None:
    """The deck opens with a red banner and treats every result as pending if this breaks."""
    for name in ("results.js", "results.sample.js"):
        text = (DECK / name).read_text()
        data = json.loads(_payload(text))
        assert isinstance(data, dict) and data, f"{name} is empty"


def test_rule_case_is_computed_not_typed() -> None:
    """Slide 7 quotes a real slot of encoder candidates; the script that finds it must exist."""
    script = DECK.parents[1] / "scripts" / "rule_vs_model_case.py"
    assert script.exists(), "scripts/rule_vs_model_case.py is the provenance of slide 7"
    case = json.loads(_payload((DECK / "results.js").read_text())).get("ruleCase") or {}
    cands = case.get("candidates") or []
    assert len(cands) >= 4, "slide 7 has no candidates to show"
    assert any(c["passes"] for c in cands) and any(not c["passes"] for c in cands), (
        "the slide only makes sense if the hand rules keep some candidates and cut others"
    )
    # the whole point of the slide: the model's favourite is one the rules threw away
    best = min(cands, key=lambda c: c["risk"])
    kept = [c for c in cands if c["passes"]]
    assert not best["passes"], "the safest candidate is no longer one the rules cut"
    assert best["risk"] < min(c["risk"] for c in kept)
