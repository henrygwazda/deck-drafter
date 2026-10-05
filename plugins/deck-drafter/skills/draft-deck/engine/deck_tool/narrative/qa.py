"""
Narrative QA: the review checkpoint before a human approves a proposed narrative.
Reuses the same rule-block formatting deck_tool/qa/judge.py already uses for slide QA,
and the same severity-resolution pattern (look up each cited rule id in active_rules.json,
default to "warn" if unresolved).
"""
from __future__ import annotations

from .. import persuasion
from ..common import call_json, fill, llm_mode
from .validate import frame_findings, persuasion_findings

_PERSUASION_CHECKS = """
This narrative was built persuasion-first: a persuasion brief (in the narrative's persuasion_brief) sets out how to sell the ask to this audience, and the arguments are the steps of its belief bridge. Alongside the soundness checks above, check the persuasion, using the persuasion rules below:
- Is there a clear want (external and internal), real stakes from the evidence, and a reframe resting on evidence?
- Does the belief bridge start from something this audience already accepts, and does each argument move one belief so the audience never has to jump?
- Is the biggest objection the one this decision-maker would actually raise, and is it answered early with evidence, not hand-waved?
- Is the ask direct, yes-or-no, and something the evidence can support?
- Does every persuasive claim (thesis, argument statements, reframe, stakes, objection answers, title, closing) still rest on cited evidence at the certainty that evidence supports? Treat any overstatement, any figure not in the cited evidence, or any manufactured urgency or social proof as a serious finding: one detected overstatement costs the whole case.
- Are the admitted limits real limits from the evidence or flags, not token flaws?
Deterministic checks already cover: figures in the brief that appear in no cited evidence, bridge steps with no argument, arguments outside the bridge, objections placed past halfway, and vague asks. Do not repeat those.

Persuasion rules (ID, severity, tags, text, check):
{rules}
"""


def _severity_for(rule_id: str, active: dict) -> str:
    if rule_id.startswith("PER-"):
        return persuasion.severity(rule_id) or "warn"
    for variant_data in active["variants"].values():
        state = variant_data["rules"].get(rule_id) or variant_data["brand_rules"].get(rule_id)
        if state:
            return state.get("severity", "warn")
    return "warn"


def qa_narrative(narrative: dict, evidence: list[dict], flags: list[dict], profile: dict,
                  rules: str, active: dict) -> list[dict]:
    """Deterministic deck-frame findings first, then the model's judgment findings."""
    findings = frame_findings(narrative)
    is_persuasion = persuasion.method(narrative) == "persuasion"
    if is_persuasion:
        findings += persuasion_findings(narrative, evidence, profile)
    if llm_mode() == "stub":
        return findings
    checks = _PERSUASION_CHECKS.format(rules=persuasion.rule_block("narrative", "frame", "qa")) if is_persuasion else ""
    data = call_json(fill(
        "qa_narrative.md",
        NARRATIVE=narrative, EVIDENCE=evidence, FLAGS=flags, PROFILE=profile, RULES=rules,
        PERSUASION_CHECKS=checks,
    ))
    for f in data.get("findings", []):
        rid = f.get("rule", "")
        findings.append({
            "rule": rid, "field": f.get("field", ""), "problem": f.get("problem", ""),
            "fix": f.get("fix", ""), "severity": _severity_for(rid, active),
        })
    return findings
