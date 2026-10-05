"""
Information patterns (brief section 10). The vocabulary lives in the layout manifest,
the contract every stage shares, so the schema, extraction, and layout selection can
never disagree about what the patterns are.

Evidence carries information_pattern when extraction sets it. Specs ingested before
the field existed, or items extraction left unclassified, fall back to a pattern
inferred from kind -- a coarser signal, reported as inferred so a decision log can say
which it was.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from .common import ROOT

MANIFEST = ROOT / "template" / "layout_manifest.json"


@lru_cache(maxsize=1)
def vocabulary() -> dict:
    return json.loads(MANIFEST.read_text())["information_patterns"]


def pattern_ids() -> list[str]:
    return list(vocabulary())


_KIND_DEFAULT = {
    "big_number": "quantitative_result",
    "comparison": "comparison",
    "diagram": "process_sequence",
    "table": "evidence_matrix",
    "text": "contextual_explanation",
}

# Category labels that name ordered time periods: weeks, months, quarters, years.
_TIME_LABEL = re.compile(
    r"^\s*(week|wk|month|day|quarter|year|fy|cy|h[12]|q[1-4])\b|^\s*(19|20)\d\d\b|"
    r"^\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
    re.I)


def _chart_default(chart: dict | None) -> str:
    chart = chart or {}
    if chart.get("type") == "line":
        return "temporal_trend"
    if chart.get("type") == "scatter":
        return "relationship"
    cats = chart.get("categories") or []
    if len(cats) >= 2 and all(isinstance(c, str) and _TIME_LABEL.search(c) for c in cats):
        return "temporal_trend"
    return "comparison"


def evidence_pattern(ev: dict) -> tuple[str, str]:
    """(pattern, how) where how is "extracted" or "inferred from kind"."""
    p = ev.get("information_pattern")
    if p in vocabulary():
        return p, "extracted"
    kind = ev.get("kind")
    if kind == "chart":
        return _chart_default(ev.get("chart")), "inferred from kind"
    return _KIND_DEFAULT.get(kind, "contextual_explanation"), "inferred from kind"
