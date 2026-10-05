"""
KB rule retrieval for slide content generation. slide_spec-tagged CLM (headline/claim
phrasing) and EVD (evidence framing on a slide) are the core; AUD and SPW (word-budget/
density -- distinct from CLM/EVD, not redundant) are pulled in full. DSN is excluded
except DSN-05 (bulleted lists kept to 3-5 items, content-level), since the other
slide_spec-tagged DSN rules are rendering mechanics (chart/color/photo specifics) the
manifest and renderer already encode structurally -- content generation can't act on them.
Also pulls deck_tool.editorial's curated selection-judgment rules, since compose's prose
generation benefits from the same inclusion/exclusion guidance narrative uses.
"""
from __future__ import annotations

from ..activate import rules_for_stage, variants
from ..editorial.kb_rules import editorial_rule_lines

_INCLUDE_CATEGORIES = {"CLM", "EVD", "AUD", "SPW"}
_EXTRA_RULES = {"DSN-05"}


def compose_rule_lines(active: dict) -> dict[str, str]:
    lines: dict[str, str] = {}
    for v in variants(active["profile"]):
        for line in rules_for_stage(active, v, "slide_spec").splitlines():
            if not line.strip():
                continue
            rid = line.split()[0]
            category = rid.split("-")[0]
            if category in _INCLUDE_CATEGORIES or rid in _EXTRA_RULES:
                lines.setdefault(rid, line)
    for rid, line in editorial_rule_lines(active).items():
        lines.setdefault(rid, line)
    return lines


def compose_rules(active: dict, evidence: list[dict] | None = None) -> str:
    from ..retrieval import select
    return "\n".join(select(compose_rule_lines(active), evidence).values())


def compose_rule_ids(active: dict) -> list[str]:
    return sorted(compose_rule_lines(active).keys())
