"""
Milestone 5d: tiered knowledge-base retrieval (brief section 11).

Every rule gets one tier, derived from its own applies_when text so the assignment is
auditable rather than hand-picked:

- core: applies "always". Loaded for every stage the rule is tagged with.
- conditional: triggered by audience, delivery, or setting (live, read, executive,
  investor, time limit...). Activation already resolves these against the profile,
  so an active conditional rule is loaded for its stages.
- specialized: triggered by a kind of content (scientific, financial, technical,
  quantitative). Loaded only when the deck's evidence contains that kind of content.
- stage: everything else. Loaded for its stages.

The full knowledge base stays reachable: nothing is deleted, the tier only decides
whether a rule is loaded into a given prompt, and `full` mode loads everything as
before. Within each tier, rules with an empirical evidence basis are listed first,
so research findings on perception and comprehension keep their weight relative to
practitioner advice. Retrieval mode is chosen with DECK_TOOL_RETRIEVAL (full | tiered).
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

from .kb import load_kb

_CONDITIONAL = re.compile(
    r"\blive\b|present(er|ed|ing)|slidedoc|read(-| )alone|pre-read|asynchron|audience is|executive|board|"
    r"non-native|investor|skeptic|hostile|time limit|minutes|webinar|virtual|remote|printed|distributed", re.I)

TOPICS = {
    "scientific": re.compile(r"scientif|research|study|studies|statistic|p-value|confidence interval|uncertaint|"
                             r"experiment|clinical|mechanism|hypothes|sample size|error bar|replicat|peer", re.I),
    "financial": re.compile(r"financ|revenue|cost|valuation|investor|budget|roi\b|return on|pricing|forecast|"
                            r"market size|margin", re.I),
    "technical": re.compile(r"technical|code|software|api\b|architecture|engineering|jargon|acronym", re.I),
    "quantitative": re.compile(r"chart|graph|axis|axes|plot|table|data|numer|percent|figure|scale", re.I),
}

# Content signals in a deck's evidence for each specialized topic.
_SIGNALS = {
    "scientific": re.compile(r"assay|enzyme|protein|cell|clinical|trial|study|experiment|statistic|mechanism|"
                             r"hypothes|variant|mutation|sample|laborator|\blab\b|biolog|chemi", re.I),
    "financial": re.compile(r"\$|dollar|revenue|cost|budget|price|valuation|margin|funding|invest|pound|euro", re.I),
    "technical": re.compile(r"software|\bapi\b|code|architecture|algorithm|platform|integration|pipeline", re.I),
}


def tier_of(rule: dict) -> tuple[str, list[str]]:
    """(tier, topics). Topics are set only for specialized rules."""
    aw = rule.get("applies_when", "").lower()
    if aw.startswith("always"):
        return "core", []
    if _CONDITIONAL.search(aw):
        return "conditional", []
    topics = [t for t, rx in TOPICS.items() if rx.search(aw)]
    return ("specialized", topics) if topics else ("stage", [])


@lru_cache(maxsize=1)
def tiers() -> dict[str, dict]:
    _, rules = load_kb()
    out = {}
    for rid, r in rules.items():
        tier, topics = tier_of(r)
        out[rid] = {"tier": tier, "topics": topics, "basis": r.get("evidence_basis", "")}
    return out


def deck_topics(evidence: list[dict] | None) -> set[str] | None:
    """Specialized topics the deck's evidence contains. None means unknown, in which
    case every specialized rule is loaded."""
    if evidence is None:
        return None
    found = set()
    text = " ".join(f"{e.get('text_live', '')} {e.get('text_read', '')}" for e in evidence)
    for topic, rx in _SIGNALS.items():
        if rx.search(text):
            found.add(topic)
    if any(e.get("kind") in ("big_number", "chart", "table", "comparison") for e in evidence):
        found.add("quantitative")
    return found


def mode() -> str:
    return os.environ.get("DECK_TOOL_RETRIEVAL", "full")


def keep(rid: str, topics: set[str] | None) -> bool:
    """Whether tiered retrieval loads a rule that activation left active for a stage.
    Brand entries and unknown ids are always kept."""
    t = tiers().get(rid)
    if t is None or t["tier"] != "specialized" or topics is None:
        return True
    return bool(set(t["topics"]) & topics)


def order(rule_ids) -> list[str]:
    """Tier order (core, conditional, specialized, stage), empirical first within each."""
    rank = {"core": 0, "conditional": 1, "specialized": 2, "stage": 3}
    basis = {"empirical": 0, "experience": 1, "argument": 2}
    t = tiers()
    return sorted(rule_ids, key=lambda r: (rank.get(t.get(r, {}).get("tier"), 4),
                                           basis.get(t.get(r, {}).get("basis"), 3), r))


def select(lines: dict[str, str], evidence: list[dict] | None) -> dict[str, str]:
    """Apply the retrieval mode to an {id: prompt line} rule block."""
    if mode() != "tiered":
        return lines
    topics = deck_topics(evidence)
    t = tiers()
    out = {}
    for rid in order(r for r in lines if keep(r, topics)):
        tag = t.get(rid, {})
        line = lines[rid]
        if tag.get("basis") == "empirical" and "[empirical]" not in line:
            line = line.replace("]", ", empirical]", 1) if "]" in line else line
        out[rid] = line
    return out


def summary(lines: dict[str, str], evidence: list[dict] | None) -> dict:
    """Counts for the comparison write-up: per tier, loaded under full and tiered."""
    topics = deck_topics(evidence)
    t = tiers()
    counts = {}
    for rid in lines:
        tier = t.get(rid, {}).get("tier", "brand")
        c = counts.setdefault(tier, {"full": 0, "tiered": 0})
        c["full"] += 1
        c["tiered"] += keep(rid, topics)
    return {"topics": sorted(topics) if topics is not None else None, "by_tier": counts,
            "full": len(lines), "tiered": sum(keep(r, topics) for r in lines)}
