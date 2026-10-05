"""
Per-document evidence extraction. One model call per source document -- cross-document
comparison is deck_tool.ingest.contradictions's job, not extraction's. Audience-blind
by design: extraction never takes a profile, since filtering "what's worth capturing"
by relevance to a purpose is an editorial judgment reserved for a later stage.
"""
from __future__ import annotations

import re

from ..common import call_json, fill, llm_mode
from ..patterns import vocabulary
from .readers import MARKER_RE as _MARKER_RE

_NUMBER_RE = re.compile(r"\d")
_UNIT_RE = re.compile(r"\b\d+(\.\d+)?\s*(minutes?|hours?|%|samples?|errors?)\b", re.I)


def _stub_extract(raw_text: str) -> list[dict]:
    """Crude, deterministic heuristic so the pipeline can be exercised offline:
    one item per marked line that contains a digit, kind guessed from a unit word."""
    items = []
    for line in raw_text.split("\n"):
        m = _MARKER_RE.match(line)
        if not m:
            continue
        marker, content = m.group(1), m.group(2).strip()
        if not content or not _NUMBER_RE.search(content):
            continue
        kind = "big_number" if _UNIT_RE.search(content) else "text"
        items.append({
            "kind": kind, "claim_type": None, "text_live": content, "text_read": content,
            "locator": marker, "original_value": None, "unit": "", "transform": "",
            "verification": "unverified",
        })
    return items


def extract_evidence(source: dict, raw_text: str) -> list[dict]:
    if llm_mode() == "stub":
        return _stub_extract(raw_text)
    patterns = {pid: v["description"] for pid, v in vocabulary().items()}
    data = call_json(fill("extract_evidence.md", SOURCE=source, TEXT=raw_text, PATTERNS=patterns))
    return data.get("items", [])
