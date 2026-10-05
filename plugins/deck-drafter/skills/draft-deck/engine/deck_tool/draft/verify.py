"""
Deterministic checks that hold Claude's draft to its sources. No model call.

1. Quotes: every evidence item carries a verbatim quote. It is searched for in the
   source's marked text (whitespace, case, quote marks, and dashes normalised). Found:
   the locator is checked against where it was found and corrected if wrong. Not found:
   the item is marked unverified and queued for review -- kept, never silently dropped.
2. Numbers: every figure in an evidence item (its value, its wording, any chart or table
   payload) must appear in its source document. Figures that do not are listed.
3. Slide figures: every figure on a slide must appear in the evidence that slide cites
   (the title, summary, and closing may draw on any evidence). Bracketed placeholders
   like [X] or [$TBD] are exempt: they are how a draft marks a figure it does not have.
"""
from __future__ import annotations

import re

from ..ingest.payloads import _NUM_RE, _norm, _payload_numbers, source_numbers
from ..ingest.readers import MARKER_RE

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                         "−": "-", " ": " ", "…": "..."})
_BRACKETED = re.compile(r"\[[^\]]*\]")
_ID_IN_TEXT = re.compile(r"\b(?:EV|SRC|SL|CL|ARG|SEC|FLAG|OBJ|UQ)\d{2,}\b")


def norm_text(s: str) -> str:
    s = (s or "").translate(_QUOTES).lower()
    s = re.sub(r"[*_`#>|]", " ", s)  # markdown emphasis and table pipes are not part of the words
    return " ".join(s.split())


def marked_lines(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        m = MARKER_RE.match(line)
        out.append((m.group(1), m.group(2)) if m else ("", line))
    return out


def find_quote(quote: str, text: str) -> list[str]:
    """Markers of the lines the quote spans, or [] if it is not in the text. A quote may
    run across line breaks; lines are joined with a space for the search."""
    q = norm_text(quote).strip(" .\"'")
    if len(q) < 3:
        return []
    joined, spans = "", []
    for marker, body in marked_lines(text):
        b = norm_text(body)
        if not b:
            continue
        start = len(joined) + (1 if joined else 0)
        joined = f"{joined} {b}" if joined else b
        spans.append((start, len(joined), marker))
    i = joined.find(q)
    if i < 0:
        return []
    j = i + len(q)
    return [m for s, e, m in spans if s < j and e > i]


def locator_matches(locator: str, markers: list[str]) -> bool:
    loc = locator or ""
    for m in markers:
        if m and re.search(rf"(?<![\w]){re.escape(m)}(?![\d])", loc):
            return True
    return False


def locator_from(markers: list[str]) -> str:
    markers = [m for m in markers if m]
    if not markers:
        return ""
    return markers[0] if len(markers) == 1 else f"{markers[0]}-{markers[-1]}"


# A digit run glued to letters is a name, not a figure: HX-212, Q3, COVID-19, A1.
_IDENTIFIER = re.compile(r"\b[A-Za-z]+-?\d+[A-Za-z]*\b")


def numbers_in(text: str, skip_placeholders: bool = True) -> set[str]:
    t = _BRACKETED.sub(" ", text or "") if skip_placeholders else (text or "")
    t = _IDENTIFIER.sub(" ", t)
    return {n for n in (_norm(x) for x in _NUM_RE.findall(t)) if n is not None}


_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}
_WORDS.update({"thirty": "30", "forty": "40", "fifty": "50", "hundred": "100", "half": "0.5", "dozen": "12"})
_WORD_RE = re.compile(r"\b(" + "|".join(_WORDS) + r")\b", re.I)


def word_numbers(text: str) -> set[str]:
    """Figures written as words ("about five weeks"), so a slide that writes the same
    figure as a numeral ("5 weeks", as the brand asks) still traces to its source."""
    return {_WORDS[m.lower()] for m in _WORD_RE.findall(text or "")}


def evidence_numbers(ev: dict) -> set[str]:
    nums = set()
    for k in ("text_live", "text_read", "value", "label", "quote"):
        nums |= numbers_in(str(ev.get(k) or ""), skip_placeholders=False)
        nums |= word_numbers(str(ev.get(k) or ""))
    for k in ("chart", "table", "comparison", "diagram"):
        if isinstance(ev.get(k), dict):
            nums |= set(_payload_numbers(ev[k]))
    return nums


def unsupported_numbers(ev: dict, source_text: str) -> list[str]:
    known = source_numbers(source_text) | word_numbers(source_text)
    return sorted(n for n in evidence_numbers(ev) if n not in known)


def visible_ids(text: str) -> list[str]:
    return sorted(set(_ID_IN_TEXT.findall(text or "")))
