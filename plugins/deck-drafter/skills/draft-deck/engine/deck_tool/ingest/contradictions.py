"""
Contradiction detection across the full flattened evidence list -- run once, after
every source document has been extracted, since comparing across documents is this
module's job, not extraction's.
"""
from __future__ import annotations

import re
from collections import defaultdict

from ..common import call_json, fill, llm_mode

_NUMBER_RE = re.compile(r"-?\d+(\.\d+)?")


def _number_in(text: str) -> float | None:
    m = _NUMBER_RE.search(text or "")
    return float(m.group()) if m else None


def _stub_detect(evidence: list[dict]) -> list[dict]:
    """Crude, deterministic heuristic: group by claim_type, flag any group whose
    members disagree on the numeric value in text_live."""
    groups: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for e in evidence:
        claim_type = e.get("claim_type")
        num = _number_in(e.get("text_live", ""))
        if not claim_type or num is None:
            continue
        groups[claim_type].append((e["id"], num))

    flags = []
    for claim_type, items in groups.items():
        values = sorted({v for _, v in items})
        if len(values) > 1:
            flags.append({
                "type": "conflicting_sources",
                "evidence": [eid for eid, _ in items],
                "note": f"Items tagged '{claim_type}' report different values: {values}.",
            })
    return flags


def detect_contradictions(evidence: list[dict], sources: list[dict]) -> list[dict]:
    if llm_mode() == "stub":
        return _stub_detect(evidence)
    data = call_json(fill("detect_contradictions.md", EVIDENCE=evidence, SOURCES=sources))
    return data.get("flags", [])
