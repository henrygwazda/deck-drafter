"""
Persuasion layer (kb/persuasion_v1.json): rules curated in our own words from Henry's
marketing-persuasion-guide, each citing the guide's section heading. It sits beside the
presentation KB rather than inside rules_v2.json, so the KB stays the record of its own
sources and the persuasion layer can be switched off for an evidence-first comparison.

Narrative method: "persuasion" (default) builds a persuasion brief and treats the
arguments as steps of a belief bridge. "evidence" is the earlier evidence-first method,
kept so the two can be compared from the same ingested evidence.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

from .common import ROOT

PATH = ROOT / "kb" / "persuasion_v1.json"
STAGES = ("persuasion_brief", "narrative", "frame", "slide_copy", "qa")
METHODS = ("persuasion", "evidence")


@lru_cache(maxsize=1)
def load() -> dict:
    return json.loads(PATH.read_text())


def rules() -> list[dict]:
    return load()["rules"]


def rule(rid: str) -> dict | None:
    return next((r for r in rules() if r["id"] == rid), None)


def _line(r: dict) -> str:
    tag = f", {r['tag']}" if r.get("tag") else ""
    return f"{r['id']} [{r['severity']}{tag}] {r['rule']} Check: {r['check']}"


def rule_lines(*stages: str) -> dict[str, str]:
    """{rule_id: prompt line} for rules applying at any of the given stages, in id order."""
    want = set(stages)
    return {r["id"]: _line(r) for r in rules() if want & set(r["stages"])}


def rule_block(*stages: str) -> str:
    return "\n".join(rule_lines(*stages).values())


def severity(rid: str) -> str | None:
    r = rule(rid)
    return r["severity"] if r else None


def method(narrative: dict | None = None) -> str:
    """For an existing narrative, its recorded method (narratives written before this
    layer have none and are evidence-first). For a new one (None), DECK_TOOL_NARRATIVE,
    default persuasion."""
    if narrative is not None:
        return narrative.get("method") if narrative.get("method") in METHODS else "evidence"
    m = os.environ.get("DECK_TOOL_NARRATIVE", "persuasion")
    if m not in METHODS:
        raise SystemExit(f"DECK_TOOL_NARRATIVE must be one of {METHODS}, not {m!r}")
    return m
