"""
KB rule retrieval for editorial selection judgment: deciding what evidence or content
earns a place in the deck versus notes/appendix/exclusion, as distinct from message_model
(thesis framing), outline (sequencing), and slide_spec (how a given slide is worded).
"editorial" is a broad, heavily cross-tagged KB stage (127 of 323 rules) that mostly
restates content already pulled by those three stages; this module keeps only the subset
confirmed by direct inspection to be genuinely about inclusion/exclusion/prioritization,
not rendering mechanics (chart color, legend scale, rounding) the manifest/renderer already
own structurally. Shared by both deck_tool.narrative and deck_tool.compose, since the user's
ask was explicit that editorial judgment belongs at both the narrative and slide-assembly
level, not only the latter.
"""
from __future__ import annotations

from ..activate import rules_for_stage, variants

_INCLUDE_RULES = {
    "EVD-05",  # relevant-but-not-primary evidence moves to notes/appendix, not cut or crowded in
    "EVD-32",  # only include claims the presenter can clearly explain and defend
    "EVD-61",  # disclose analytical choices behind a data-driven finding
    "EVD-62",  # keep the full dataset/analysis available in notes/appendix for consequential claims
    "EVD-66",  # choose evidence for relevance to the claim, not recency
    "DAT-38",  # don't hide a dimension that could change the audience's conclusion
    "SPW-06",  # test whether a short summary suffices before building a full supporting pack
    "SPW-07",  # route dense, scrutiny-worthy content to notes/appendix, not the main flow
    "SPW-09",  # never put a raw data table on a live slide; appendix it
    "SPW-35",  # cut any slide not functionally necessary to the spoken narrative
    "FAIL-15", # justify every kept item by its support for the core claim, not fear of omission
    "FAIL-20", # check whether a claim is actually unclear before adding polish to fix it
    "AUD-16",  # calibrate exposure of speculative/uncertain material to audience risk tolerance
}


def editorial_rule_lines(active: dict) -> dict[str, str]:
    """{rule_id: formatted line}, unioned across every profile delivery variant."""
    lines: dict[str, str] = {}
    for v in variants(active["profile"]):
        for line in rules_for_stage(active, v, "editorial").splitlines():
            if not line.strip():
                continue
            rid = line.split()[0]
            if rid in _INCLUDE_RULES:
                lines.setdefault(rid, line)
    return lines


def editorial_rules(active: dict) -> str:
    return "\n".join(editorial_rule_lines(active).values())


def editorial_rule_ids(active: dict) -> list[str]:
    return sorted(editorial_rule_lines(active).keys())
