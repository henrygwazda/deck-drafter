"""
Slide content generation: one batched call across the whole deck, so headlines don't
repeat each other and framing stays consistent between a claim slide and any related
risks-slide row -- the same cross-item coherence need deck_tool.narrative.develop
already solved with one call across all evidence, not deck_tool.ingest.extract's
granularity (which itself runs once per source document, not per evidence item).
"""
from __future__ import annotations

import json

from .. import persuasion
from ..common import call_json, fill, llm_mode


def _slide_plan_for_prompt(slides: list[dict]) -> list[dict]:
    return [{"arg_id": s["arg_id"], "layout": s["layout"],
             "primary_evidence": s["primary_evidence"], "other_evidence": s["other_evidence"]}
            for s in slides]


def _folded_for_prompt(folded: list[dict]) -> list[dict]:
    return [{"arg_id": a["id"], "statement": a["statement"], "evidence": a.get("evidence", [])} for a in folded]


def _risks_for_prompt(risks_slide: dict | None) -> list[dict]:
    if not risks_slide:
        return []
    groups = []
    for i, group in enumerate(risks_slide["groups"]):
        items, combined_evidence = [], set()
        for item in group:
            combined_evidence |= item["evidence"]
            if item["kind"] == "argument":
                items.append({"kind": "argument", "id": item["id"], "statement": item["source"]["statement"]})
            else:
                items.append({"kind": "question", "id": item["id"], "note": item["source"]["note"]})
        groups.append({"group_index": i, "items": items, "evidence": sorted(combined_evidence)})
    return groups


def _cited_evidence_ids(slide_plan, folded, risks_groups) -> set[str]:
    ids: set[str] = set()
    for s in slide_plan:
        if s["primary_evidence"]:
            ids.add(s["primary_evidence"])
        ids.update(s["other_evidence"])
    for a in folded:
        ids.update(a["evidence"])
    for g in risks_groups:
        ids.update(g["evidence"])
    return ids


def _stub_compose(slide_plan: list[dict], risks_groups: list[dict]) -> dict:
    """Crude, deterministic heuristic for offline shape tests only -- not quality-
    representative."""
    slides = {
        s["arg_id"]: {"headline": f"Stub headline for {s['arg_id']}",
                      "body": {"live": ["Stub point"], "read": "Stub read body."},
                      "notes": ""}
        for s in slide_plan
    }
    risks = [{"group_index": g["group_index"], "text": "Stub risk text.", "mitigation": "Stub mitigation."}
             for g in risks_groups]
    return {
        "title": {"headline": "Stub title", "subhead": "Stub subhead"},
        "slides": slides,
        "risks_headline": "Stub risks headline",
        "risks": risks,
        "closing": {"headline": "Stub closing headline", "ask": "Stub ask"},
        "exec_summary": {"headline": "Stub exec summary headline", "points": ["Stub point 1", "Stub point 2", "Stub point 3"]},
        "positioning": {"headline": "Stub positioning headline"},
    }


def _str():
    return {"type": "string"}


def _obj(props: dict, required=None) -> dict:
    return {"type": "object", "properties": props, "required": required or list(props),
            "additionalProperties": False}


_SLIDE = _obj({"headline": _str(),
               "body": _obj({"live": {"type": "array", "items": _str()}, "read": _str()}),
               "notes": _str()})

# Enforced by the CLI (--json-schema). This is the largest single model output in the
# pipeline, and in real runs it failed to parse in two different ways: a brace
# mismatch in one long line, and a malformed object followed by a "Correction" and a
# second object. Constrained output removes both rather than retrying past them.
def compose_schema(frame_fixed: bool) -> dict:
    """Without title and closing when the approved narrative's deck frame fixes them."""
    if not frame_fixed:
        return COMPOSE_SCHEMA
    props = {k: v for k, v in COMPOSE_SCHEMA["properties"].items() if k not in ("title", "closing")}
    return _obj(props)


COMPOSE_SCHEMA = _obj({
    "title": _obj({"headline": _str(), "subhead": _str()}),
    "slides": {"type": "object", "additionalProperties": _SLIDE},
    "risks_headline": _str(),
    "risks": {"type": "array", "items": _obj({"group_index": {"type": "integer"}, "text": _str(),
                                              "mitigation": _str()})},
    "closing": _obj({"headline": _str(), "ask": _str()}),
    "exec_summary": _obj({"headline": _str(), "points": {"type": "array", "items": _str()}}),
    "positioning": _obj({"headline": _str()}),
})


def _positioning_task(positioning_text: str | None) -> str:
    if not positioning_text:
        return ""
    return (
        "\nThis deck also needs a positioning slide. Write only a short headline framing the category this "
        "positioning statement belongs to -- the statement itself is fixed and will not use your wording, so "
        "do not write or paraphrase the statement.\n\n"
        f'The fixed positioning statement, for context only: "{positioning_text}"\n'
    )


_FRAME_FREE_TASK = (
    "Also write the deck's title slide (a short headline naming the subject, and a one-line subhead giving "
    "the deck's angle) and its closing slide (a headline restating the thesis as a decision or "
    "recommendation, and a one-line ask naming the specific next step). Both should read as bookends of "
    "the same argument, not generic openers.")


def _frame_task(frame: dict | None) -> str:
    if not frame:
        return _FRAME_FREE_TASK
    return ("The deck's title, section structure, and closing are fixed by the approved narrative and are "
            "not yours to write; keep slide headlines consistent with them:\n"
            f"```json\n{json.dumps(frame, indent=1, ensure_ascii=False)}\n```")


def compose_slide_content(narrative: dict, slide_plan: list[dict], folded: list[dict],
                           risks_slide: dict | None, evidence_by_id: dict, sources: list[dict],
                           rules: str, positioning_text: str | None = None) -> dict:
    prompt_slide_plan = _slide_plan_for_prompt(slide_plan)
    prompt_folded = _folded_for_prompt(folded)
    prompt_risks = _risks_for_prompt(risks_slide)

    frame = narrative.get("deck_frame")
    if llm_mode() == "stub":
        return _stub_compose(prompt_slide_plan, prompt_risks)

    cited_ids = _cited_evidence_ids(prompt_slide_plan, prompt_folded, prompt_risks)
    evidence_payload = [evidence_by_id[eid] for eid in sorted(cited_ids) if eid in evidence_by_id]

    data = call_json(fill(
        "compose_slides.md",
        THESIS=narrative["thesis"], PROGRESSION=narrative["narrative_progression"],
        SLIDE_PLAN=prompt_slide_plan, FOLDED=prompt_folded, RISKS=prompt_risks,
        EVIDENCE=evidence_payload, SOURCES=sources, RULES=rules,
        POSITIONING_TASK=_positioning_task(positioning_text), FRAME_TASK=_frame_task(frame),
    ), schema=compose_schema(frame is not None))
    return {
        "title": data.get("title", {"headline": "", "subhead": ""}),
        "slides": data.get("slides", {}),
        "risks_headline": data.get("risks_headline", ""),
        "risks": data.get("risks", []),
        "closing": data.get("closing", {"headline": "", "ask": ""}),
        "exec_summary": data.get("exec_summary", {"headline": "", "points": []}),
        "positioning": data.get("positioning", {"headline": ""}),
    }


# ---- Milestone 5c: content for a planner-built deck (deck_tool.compose.planner) ----

_BODY_GUIDANCE = {
    "claim_text": "exactly three short reasons in body.live, each a fragment of at most ten words",
    "claim_mixed": "body.live empty or one short point; the slide shows the figure and the quoted item verbatim",
}
_DEFAULT_GUIDANCE = "at most two short points in body.live (CLM-02), each at most twelve words"

DECK_SCHEMA_RISK = _obj({"id": _str(), "text": _str(), "mitigation": _str()})


def deck_schema(frame_fixed: bool, method: str = "evidence") -> dict:
    props = {
        "title": _obj({"headline": _str(), "subhead": _str()}),
        "slides": {"type": "object", "additionalProperties": _SLIDE},
        "risks_headline": _str(),
        "risks": {"type": "array", "items": DECK_SCHEMA_RISK},
        "closing": _obj({"headline": _str(), "ask": _str()}),
        "exec_summary": _obj({"headline": _str(), "points": {"type": "array", "items": _str()}}),
        "positioning": _obj({"headline": _str()}),
    }
    if method == "persuasion":
        props["slides"] = {"type": "object", "additionalProperties": _obj({
            "headline": _str(), "subhead": _str(),
            "body": _obj({"live": {"type": "array", "items": _str()}, "read": _str()}), "notes": _str()})}
        props["exec_summary"] = _obj({"headline": _str(), "points": {"type": "array", "items": _str()},
                                      "open_note": _str(), "notes": _str()})
        props["objections_notes"] = _str()
    if frame_fixed:
        props = {k: v for k, v in props.items() if k not in ("title", "closing")}
    return _obj(props)


def _deck_plan_for_prompt(narrative: dict, plan: dict) -> tuple[list[dict], list[dict]]:
    args = {a["id"]: a for a in narrative["arguments"]}
    questions = {q["id"]: q for q in narrative.get("unresolved_questions", [])}
    slides = []
    for p in plan["slides"]:
        slides.append({
            "arg_id": p["arg_id"], "role": p["role"], "statement": args[p["arg_id"]]["statement"],
            "layout": p["layout"], "pattern": p["pattern"],
            "primary_evidence": p["primary_evidence"], "quote_evidence": p["quote_evidence"],
            "other_evidence": p["other_evidence"],
            "caveats": [{"id": c, "note": questions[c]["note"]} for c in p["caveats"] if c in questions],
            "body_guidance": _BODY_GUIDANCE.get(p["layout"], _DEFAULT_GUIDANCE),
        })
        a = args[p["arg_id"]]
        fixed = {h["argument"]: h for h in narrative.get("headline_flow") or []}.get(p["arg_id"])
        if fixed:
            slides[-1].update(headline=fixed["headline"], job=fixed.get("job", ""))
        if a.get("moves_belief") or a.get("answers_objection"):
            slides[-1].update(moves_belief=a.get("moves_belief", ""), answers_objection=a.get("answers_objection", ""))
    risks = []
    for it in plan["risks"]["live"]:
        r = {"id": it["id"], "kind": it["kind"],
             "text": it["source"].get("statement") or it["source"].get("note", ""),
             "evidence": sorted(it["evidence"])}
        if it["kind"] == "objection":
            r.update(held_by=it["source"].get("held_by", ""), answer=it["source"].get("answer", ""),
                     answer_status=it["source"].get("status", ""))
        risks.append(r)
    return slides, risks


def _brief_for_compose(brief: dict) -> dict:
    """The parts of the brief slide copy uses; the bridge and objections reach compose
    through the slide plan and the objection rows."""
    keys = ("decision_maker", "stakeholders", "ask", "want", "current_belief", "status_quo", "stakes",
            "reframe", "trust", "limits_to_admit")
    return {k: brief[k] for k in keys if k in brief}


def _stub_deck(slides: list[dict], risks: list[dict]) -> dict:
    """Deterministic stand-in for offline tests only."""
    out = {}
    for s in slides:
        n = 3 if s["layout"] == "claim_text" else (0 if s["layout"] == "claim_mixed" else 2)
        out[s["arg_id"]] = {"headline": s.get("headline") or f"Stub headline for {s['arg_id']}",
                            **({"subhead": f"Stub evidence subhead for {s['arg_id']}."} if s.get("headline") else {}),
                            "body": {"live": [f"Stub point {i + 1}" for i in range(n)],
                                     "read": "Stub read body for this claim."},
                            "notes": ""}
    return {
        "title": {"headline": "Stub title", "subhead": "Stub subhead"},
        "slides": out,
        "risks_headline": "Stub risks headline",
        "risks": [{"id": r["id"], "text": "Stub concern.", "mitigation": "Stub response."} for r in risks],
        "closing": {"headline": "Stub closing headline", "ask": "Stub ask"},
        "exec_summary": {"headline": "Stub exec summary headline", "points": ["Stub one", "Stub two", "Stub three"],
                         "open_note": "Stub: one thing is still open.", "notes": "Stub summary talk track."},
        "objections_notes": "Stub objections talk track.",
        "positioning": {"headline": "Stub positioning headline"},
    }


def compose_deck_content(narrative: dict, plan: dict, evidence_by_id: dict, sources: list[dict],
                         rules: str, positioning_text: str | None = None) -> dict:
    slides, risks = _deck_plan_for_prompt(narrative, plan)
    frame = narrative.get("deck_frame")
    if llm_mode() == "stub":
        return _stub_deck(slides, risks)
    cited = set()
    for s in slides:
        cited.update(x for x in (s["primary_evidence"], s["quote_evidence"], *s["other_evidence"]) if x)
    for r in risks:
        cited.update(r["evidence"])
    method = persuasion.method(narrative)
    common = dict(
        THESIS=narrative["thesis"], PROGRESSION=narrative["narrative_progression"],
        SLIDE_PLAN=slides, RISKS=risks,
        EVIDENCE=[evidence_by_id[e] for e in sorted(cited) if e in evidence_by_id],
        SOURCES=sources, RULES=rules,
        POSITIONING_TASK=_positioning_task(positioning_text), FRAME_TASK=_frame_task(frame),
    )
    if method == "persuasion":
        prompt = fill("compose_deck.md", **common,
                      AUDIENCE=narrative.get("audience_purpose", {}),
                      BRIEF=_brief_for_compose(narrative.get("persuasion_brief") or {}),
                      PERSUASION_RULES=persuasion.rule_block("slide_copy"))
    else:
        prompt = fill("compose_deck_evidence.md", **common)
    data = call_json(prompt, schema=deck_schema(frame is not None, method))
    return {
        "title": data.get("title", {"headline": "", "subhead": ""}),
        "slides": data.get("slides", {}),
        "risks_headline": data.get("risks_headline", ""),
        "risks": data.get("risks", []),
        "closing": data.get("closing", {"headline": "", "ask": ""}),
        "exec_summary": data.get("exec_summary", {"headline": "", "points": []}),
        "objections_notes": data.get("objections_notes", ""),
        "positioning": data.get("positioning", {"headline": ""}),
    }
