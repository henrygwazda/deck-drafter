"""
KB rule retrieval for narrative development. message_model is thesis/evidence-
sufficiency reasoning; outline is mostly argument macro-structure (situation/
complication/resolution, addressing the strongest objections) but a subset is
slide/section mechanics that don't exist yet in Phase 2's output (outline length
caps, slide titles, table of contents, delivery-time sizing) -- excluded here,
mirroring deck_tool/qa/judge.py's own DECK_RULES/KB_LINTED precedent for the same
problem. This denylist is a first pass to refine after the real run, not treated as
exhaustively correct. Also pulls deck_tool.editorial's curated selection-judgment
rules, since narrative is where evidence inclusion/exclusion is first decided.
"""
from __future__ import annotations

from ..activate import rules_for_stage, variants
from ..editorial.kb_rules import editorial_rule_lines

# NAR-08 (three to five sections), NAR-13 (section transitions), and CLM-16 (the
# closing states the main assertion) were excluded here until the narrative began
# proposing the deck frame (title, sections, closing). They now govern narrative output.
OUTLINE_SLIDE_MECHANICS = {
    "NAR-21", "NAR-23", "NAR-30", "NAR-49", "NAR-50", "CLM-01",
}


def narrative_rule_lines(active: dict) -> dict[str, str]:
    """{rule_id: formatted line}, unioned across every profile delivery variant --
    a mode:"both" profile can activate different rules per variant, and a single
    narrative must not silently miss one variant's rules."""
    lines: dict[str, str] = {}
    for v in variants(active["profile"]):
        for stage in ("message_model", "outline"):
            for line in rules_for_stage(active, v, stage).splitlines():
                if not line.strip():
                    continue
                rid = line.split()[0]
                if stage == "outline" and rid in OUTLINE_SLIDE_MECHANICS:
                    continue
                lines.setdefault(rid, line)
    for rid, line in editorial_rule_lines(active).items():
        lines.setdefault(rid, line)
    return lines


def narrative_rules(active: dict, evidence: list[dict] | None = None) -> str:
    """Newline-joined rule block, formatted the same way judge.py's prompts embed rules."""
    from ..retrieval import select
    return "\n".join(select(narrative_rule_lines(active), evidence).values())


def narrative_rule_ids(active: dict) -> list[str]:
    return sorted(narrative_rule_lines(active).keys())
