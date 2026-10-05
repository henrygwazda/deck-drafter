"""
Narrative proposal generation: given extracted evidence and ingestion flags, propose a
thesis, major arguments, unresolved questions, and a narrative progression. Takes an
optional prior narrative + free-text feedback for revision, folding both into the same
prompt rather than a second near-duplicate one -- the task is structurally identical,
just conditioned on prior output and a human correction instead of starting cold.
"""
from __future__ import annotations

import json

from .. import persuasion
from ..common import call_json, fill, llm_mode


def _stub_develop(evidence: list[dict], method: str = "evidence") -> dict:
    """Crude, deterministic heuristic for offline shape tests only -- not quality-
    representative. Picks the first two evidence items as primary arguments."""
    primary = evidence[:2]
    args = [
        {"id": f"ARG{i + 1:02d}", "statement": e.get("text_read") or e.get("text_live", ""),
         "role": "primary", "evidence": [e["id"]]}
        for i, e in enumerate(primary)
    ]
    if method == "persuasion":
        for i, a in enumerate(args):
            a.update(bridge_step=i + 2, moves_belief="Stub belief shift.",
                     answers_objection="OBJ01" if i == 0 else "")
    thesis = args[0]["statement"] if args else ""
    out = {
        "thesis": thesis,
        "arguments": args,
        "unresolved_questions": [],
        "narrative_progression": [{"step": 1, "label": "situation", "description": "",
                                   "arguments": [a["id"] for a in args]}],
        "excluded_evidence": [],
        "deck_frame": {
            "title": {"headline": "Stub deck title", "subhead": "Stub subhead"},
            "sections": [],
            "sections_rationale": "Stub: too few arguments for sections.",
            "closing": {"headline": thesis or "Stub closing", "ask": "Stub ask"},
        },
    }
    if method == "persuasion":
        out["deck_frame"]["objections_after"] = ""
        out["deck_frame"]["closing"]["ask"] = "Approve the stub proposal this week."
        out["story"] = {"problem": "Stub problem.", "why_care": "Stub reason to care.", "solution": "Stub solution.",
                        "why_best": "Stub why best.", "big_idea": "Stub big idea.", "evidence": [a["evidence"][0] for a in args]}
        out["headline_flow"] = [{"argument": a["id"], "job": "proof", "headline": f"Stub selling headline {i + 1}"}
                                for i, a in enumerate(args)]
    return out


def _stub_brief(evidence: list[dict]) -> dict:
    """Deterministic stand-in for offline tests only."""
    ev = [e["id"] for e in evidence[:2]]
    j = {"evidence": [], "status": "judgment"}
    return {
        "decision_maker": {"role": "Stub decision-maker", "accountable_for": "Stub outcome", **j},
        "stakeholders": [{"role": "Stub stakeholder", "accountable_for": "Stub", "judges_on": "Stub", **j}],
        "ask": {"statement": "Approve the stub proposal this week.", "verb": "approve", **j},
        "want": {"external": "Stub external problem.", "internal": "Stub internal worry.", "philosophical": "", **j},
        "current_belief": {"belief": "Stub belief.", "awareness_stage": "problem_aware", **j},
        "status_quo": {"alternative": "Continue as now.", "cost": "Stub cost.", "evidence": ev[:1],
                       "status": "supported" if ev else "open"},
        "stakes": {"statement": "Stub stakes.", "evidence": ev[:1], "status": "supported" if ev else "open"},
        "reframe": {"statement": "Stub reframe.", "evidence": ev[1:2], "status": "supported" if len(ev) > 1 else "open"},
        "belief_bridge": [{"step": i + 1, "belief": f"Stub belief {i + 1}.", "already_held": i == 0,
                           "evidence": ev[:1], "status": "supported" if ev else "open"} for i in range(3)],
        "objections": [{"id": "OBJ01", "rank": 1, "held_by": "Stub decision-maker", "objection": "Stub objection.",
                        "answer": "Stub answer.", "evidence": ev[:1], "status": "supported" if ev else "open"}],
        "trust": {"empathy": "Stub empathy.", "competence": "Stub competence.", "evidence": ev[:1],
                  "status": "supported" if ev else "open"},
        "limits_to_admit": [{"limit": "Stub limit.", "why_credible": "Stub.", "linked_flag": None,
                             "evidence": ev[:1], "status": "supported" if ev else "open"}],
        "open_questions": [],
    }


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_S, _IDS = {"type": "string"}, {"type": "array", "items": {"type": "string"}}

# Enforced by the CLI (--json-schema) for the same reason as compose's schema: a
# narrative over a large evidence set is one long JSON object, the shape that failed
# to parse in real runs. Field values (roles, id formats, evidence resolution) are
# still checked by narrative.validate, which owns those rules.
NARRATIVE_SCHEMA = _obj({
    "thesis": _S,
    "arguments": {"type": "array", "items": _obj({"id": _S, "statement": _S, "role": _S, "evidence": _IDS})},
    "unresolved_questions": {"type": "array", "items": _obj({
        "id": _S, "note": _S, "linked_flag": {"type": ["string", "null"]}, "evidence": _IDS, "status": _S})},
    "narrative_progression": {"type": "array", "items": _obj({
        "step": {"type": "integer"}, "label": _S, "description": _S, "arguments": _IDS})},
    "excluded_evidence": {"type": "array", "items": _obj({"evidence": _S, "reason": _S})},
    "deck_frame": _obj({
        "title": _obj({"headline": _S, "subhead": _S}),
        "sections": {"type": "array", "items": _obj({
            "id": _S, "title": _S, "question": _S, "summary": _S, "arguments": _IDS})},
        "sections_rationale": _S,
        "closing": _obj({"headline": _S, "ask": _S}),
    }),
})


_E = {"evidence": _IDS, "status": _S}
_NULLABLE = {"type": ["string", "null"]}

# The persuasion brief (prompts/develop_brief.md). Value rules -- status values, evidence
# resolution, awareness stages, objection ranks -- are checked by narrative.validate.
BRIEF_SCHEMA = _obj({
    "decision_maker": _obj({"role": _S, "accountable_for": _S, **_E}),
    "stakeholders": {"type": "array", "items": _obj({"role": _S, "accountable_for": _S, "judges_on": _S, **_E})},
    "ask": _obj({"statement": _S, "verb": _S, **_E}),
    "want": _obj({"external": _S, "internal": _S, "philosophical": _S, **_E}),
    "current_belief": _obj({"belief": _S, "awareness_stage": _S, **_E}),
    "status_quo": _obj({"alternative": _S, "cost": _S, **_E}),
    "stakes": _obj({"statement": _S, **_E}),
    "reframe": _obj({"statement": _S, **_E}),
    "belief_bridge": {"type": "array", "items": _obj({
        "step": {"type": "integer"}, "belief": _S, "already_held": {"type": "boolean"}, **_E})},
    "objections": {"type": "array", "items": _obj({
        "id": _S, "rank": {"type": "integer"}, "held_by": _S, "objection": _S, "answer": _S, **_E})},
    "trust": _obj({"empathy": _S, "competence": _S, **_E}),
    "limits_to_admit": {"type": "array", "items": _obj({
        "limit": _S, "why_credible": _S, "linked_flag": _NULLABLE, **_E})},
    "open_questions": {"type": "array", "items": _obj({"element": _S, "note": _S})},
})

# Persuasion-first narrative: arguments are belief-bridge steps, and the frame names
# where the ranked objections are answered.
STORY_SCHEMA = _obj({"problem": _S, "why_care": _S, "solution": _S, "why_best": _S, "big_idea": _S,
                     "evidence": _IDS})
HEADLINE_FLOW_SCHEMA = {"type": "array", "items": _obj({"argument": _S, "job": _S, "headline": _S})}

# The story comes first in the schema so it is written before the arguments; the
# headline flow comes last, once the sections exist.
PERSUASION_NARRATIVE_SCHEMA = _obj({
    "story": STORY_SCHEMA,
    **NARRATIVE_SCHEMA["properties"],
    "arguments": {"type": "array", "items": _obj({
        "id": _S, "statement": _S, "role": _S, "bridge_step": {"type": "integer"},
        "moves_belief": _S, "answers_objection": _S, "evidence": _IDS})},
    "deck_frame": _obj({**NARRATIVE_SCHEMA["properties"]["deck_frame"]["properties"], "objections_after": _S}),
    "headline_flow": HEADLINE_FLOW_SCHEMA,
})


def develop_brief(evidence: list[dict], sources: list[dict], flags: list[dict], profile: dict,
                  prior: dict | None = None, feedback: str | None = None) -> dict:
    """The persuasion brief, produced before the argument (PERSUASION_RUN P2)."""
    if llm_mode() == "stub":
        return _stub_brief(evidence)
    prompt = fill(
        "develop_brief.md",
        EVIDENCE=evidence, SOURCES=sources, FLAGS=flags, PROFILE=profile,
        RULES=persuasion.rule_block("persuasion_brief"),
        REVISION_CONTEXT=_revision_context(prior, feedback, what="persuasion brief"),
    )
    return call_json(prompt, schema=BRIEF_SCHEMA)


def _revision_context(prior: dict | None, feedback: str | None, what: str = "narrative proposal") -> str:
    if not prior:
        return ""
    return (
        f"This is a revision. The previous {what} was:\n"
        f"```json\n{json.dumps(prior, indent=1)}\n```\n"
        f"The human reviewing it gave this feedback: {feedback!r}\n"
        "Address the feedback directly in your revised proposal.\n\n"
    )


def develop_narrative(evidence: list[dict], sources: list[dict], flags: list[dict], profile: dict,
                       rules: str, prior: dict | None = None, feedback: str | None = None,
                       method: str = "evidence", brief: dict | None = None) -> dict:
    """method "evidence" is the original evidence-first prompt (develop_narrative_evidence.md).
    method "persuasion" builds the argument as the brief's belief bridge (develop_narrative.md)."""
    if llm_mode() == "stub":
        return _stub_develop(evidence, method)
    if method == "persuasion":
        prompt = fill(
            "develop_narrative.md",
            BRIEF=brief or {}, EVIDENCE=evidence, SOURCES=sources, FLAGS=flags, PROFILE=profile, RULES=rules,
            PERSUASION_RULES=persuasion.rule_block("narrative", "frame"),
            REVISION_CONTEXT=_revision_context(prior, feedback),
        )
        data = call_json(prompt, schema=PERSUASION_NARRATIVE_SCHEMA)
    else:
        prompt = fill(
            "develop_narrative_evidence.md",
            EVIDENCE=evidence, SOURCES=sources, FLAGS=flags, PROFILE=profile, RULES=rules,
            REVISION_CONTEXT=_revision_context(prior, feedback),
        )
        data = call_json(prompt, schema=NARRATIVE_SCHEMA)
    return {
        "thesis": data.get("thesis", ""),
        "arguments": data.get("arguments", []),
        "unresolved_questions": data.get("unresolved_questions", []),
        "narrative_progression": data.get("narrative_progression", []),
        "excluded_evidence": data.get("excluded_evidence", []),
        "deck_frame": data.get("deck_frame"),
        **({"story": data["story"], "headline_flow": data.get("headline_flow", [])} if "story" in data else {}),
    }
