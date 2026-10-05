"""
Hand-written validation for narrative_spec (not a full JSON Schema): this artifact has
exactly one producer and one in-scope consumer right now, and its shape will keep
moving once an outline/slide_spec stage exists to reference it. Checks what actually
matters today: required keys, unique argument ids, every evidence reference resolving
against the ingested spec's real ids, and a known approval status.
"""
from __future__ import annotations

import re

REQUIRED_KEYS = {
    "schema_version", "deck_id", "version", "status", "approval", "audience_purpose",
    "thesis", "arguments", "unresolved_questions", "narrative_progression",
    "excluded_evidence", "kb_rules_considered", "version_history",
}
VALID_APPROVAL_STATUSES = {"pending", "quick_draft_accepted", "approved"}
VALID_ROLES = {"primary", "supporting", "counterargument", "qualification"}
_ARG_ID_RE = re.compile(r"^ARG\d+$")
_SEC_ID_RE = re.compile(r"^SEC\d+$")
MAIN_ROLES = {"primary", "supporting"}
# CLM-16: a closing slide states the main assertion, never a placeholder.
_GENERIC_CLOSING = re.compile(r"^\s*(questions?|thank(s| you)|q\s*&\s*a|discussion|the end|next steps)[\s.!?]*$", re.I)


def validate_narrative_spec(data: dict, known_evidence_ids: set[str]) -> list[str]:
    errors = []

    missing = REQUIRED_KEYS - data.keys()
    if missing:
        errors.append(f"missing required keys: {sorted(missing)}")
        return errors  # nothing else is safe to check without these

    arg_ids = [a.get("id", "") for a in data["arguments"]]
    if len(arg_ids) != len(set(arg_ids)):
        errors.append(f"duplicate argument ids: {arg_ids}")

    for a in data["arguments"]:
        aid = a.get("id", "")
        if not _ARG_ID_RE.match(aid):
            errors.append(f"bad argument id: {aid!r}")
        if a.get("role") not in VALID_ROLES:
            errors.append(f"argument {aid}: unknown role {a.get('role')!r}")
        for eid in a.get("evidence", []):
            if eid not in known_evidence_ids:
                errors.append(f"argument {aid} references unknown evidence {eid!r}")

    for uq in data["unresolved_questions"]:
        for eid in uq.get("evidence", []):
            if eid not in known_evidence_ids:
                errors.append(f"unresolved_question {uq.get('id')} references unknown evidence {eid!r}")

    for ex in data["excluded_evidence"]:
        eid = ex.get("evidence", "")
        if eid not in known_evidence_ids:
            errors.append(f"excluded_evidence references unknown evidence {eid!r}")

    status = data.get("approval", {}).get("status")
    if status not in VALID_APPROVAL_STATUSES:
        errors.append(f"unknown approval status: {status!r}")

    if data.get("deck_frame") is not None:
        errors += validate_deck_frame(data["deck_frame"], set(arg_ids))

    if data.get("method", "evidence") == "persuasion":
        if not isinstance(data.get("persuasion_brief"), dict):
            errors.append("a persuasion-first narrative needs a persuasion_brief")
        else:
            errors += validate_brief(data["persuasion_brief"], known_evidence_ids)
            errors += validate_persuasion_links(data)

    return errors


def validate_deck_frame(frame: dict, arg_ids: set[str]) -> list[str]:
    """Structural errors only: a frame that fails these cannot be composed. Coverage and
    ordering problems are judgment-adjacent and reported by frame_findings() instead, so
    a model slip there goes to review rather than failing the run."""
    errors = []
    for part, keys in (("title", ("headline",)), ("closing", ("headline", "ask"))):
        for k in keys:
            if not (frame.get(part) or {}).get(k, "").strip():
                errors.append(f"deck_frame.{part}.{k} is empty")
    sec_ids = [sec.get("id", "") for sec in frame.get("sections", [])]
    if len(sec_ids) != len(set(sec_ids)):
        errors.append(f"duplicate section ids: {sec_ids}")
    for sec in frame.get("sections", []):
        if not _SEC_ID_RE.match(sec.get("id", "")):
            errors.append(f"bad section id: {sec.get('id')!r}")
        if not sec.get("title", "").strip():
            errors.append(f"section {sec.get('id')} has no title")
        for aid in sec.get("arguments", []):
            if aid not in arg_ids:
                errors.append(f"section {sec.get('id')} references unknown argument {aid!r}")
    return errors


def frame_findings(narrative: dict) -> list[dict]:
    """Deterministic deck-frame checks, shaped like narrative QA findings."""
    frame = narrative.get("deck_frame")
    if not frame:
        return [{"rule": "NAR-01", "field": "deck_frame", "severity": "block",
                 "problem": "The narrative has no deck frame (title, sections, closing).",
                 "fix": "Revise the narrative so it proposes a deck frame."}]
    out = []
    args = {a["id"]: a for a in narrative["arguments"]}
    sections = frame.get("sections", [])
    placement: dict[str, list[str]] = {}
    for sec in sections:
        for aid in sec.get("arguments", []):
            placement.setdefault(aid, []).append(sec["id"])
    if sections:
        for aid, a in args.items():
            where = placement.get(aid, [])
            if a["role"] in MAIN_ROLES and not where:
                out.append({"rule": "NAR-09", "field": f"deck_frame:{aid}", "severity": "block",
                            "problem": f"{aid} ({a['role']}) is in no section, so it would have no place in the deck.",
                            "fix": f"Place {aid} in the section whose question it answers."})
        for aid, where in placement.items():
            if len(where) > 1:
                out.append({"rule": "NAR-09", "field": f"deck_frame:{aid}", "severity": "block",
                            "problem": f"{aid} appears in more than one section ({', '.join(where)}).",
                            "fix": "Keep each argument in exactly one section so sections do not overlap."})
        if len(sections) > 5:
            out.append({"rule": "NAR-08", "field": "deck_frame.sections", "severity": "warn",
                        "problem": f"{len(sections)} sections, more than the three to five NAR-08 recommends.",
                        "fix": "Regroup into three to five sections."})
        first_step = {}
        for step in narrative.get("narrative_progression", []):
            for aid in step.get("arguments", []):
                first_step.setdefault(aid, step.get("step", 0))
        order = [min((first_step[a] for a in sec.get("arguments", []) if a in first_step), default=None)
                 for sec in sections]
        known = [o for o in order if o is not None]
        if known != sorted(known):
            out.append({"rule": "NAR-04", "field": "deck_frame.sections", "severity": "warn",
                        "problem": "Section order does not follow the order the narrative progression introduces their arguments.",
                        "fix": "Reorder sections to follow the progression, or revise the progression."})
    elif not frame.get("sections_rationale", "").strip():
        out.append({"rule": "NAR-08", "field": "deck_frame.sections", "severity": "warn",
                    "problem": "No sections and no stated reason for having none.",
                    "fix": "Group the arguments into sections, or say why the deck is short enough not to need them."})
    for part, key, limit, unit, rule in (("title", "headline", 60, "chars", "NAR-14"),
                                         ("closing", "headline", 100, "chars", "CLM-16"),
                                         ("closing", "ask", 20, "words", "NAR-18")):
        text = (frame.get(part) or {}).get(key, "")
        size = len(text.split()) if unit == "words" else len(text)
        if size > limit:
            out.append({"rule": rule, "field": f"deck_frame.{part}.{key}", "severity": "warn",
                        "problem": f"The {part} {key} is {size} {unit}, more than the {limit} its slide holds.",
                        "fix": f"Shorten it to {limit} {unit} or fewer and move conditions into the arguments."})
    if _GENERIC_CLOSING.match((frame.get("closing") or {}).get("headline", "")):
        out.append({"rule": "CLM-16", "field": "deck_frame.closing", "severity": "block",
                    "problem": "The closing headline is a placeholder rather than the deck's main assertion.",
                    "fix": "State the main assertion or decision in the closing headline."})
    return out


# ---- Persuasion brief and persuasion-first narrative (kb/persuasion_v1.json) ----

BRIEF_STATUSES = {"supported", "judgment", "open"}
AWARENESS_STAGES = {"unaware", "problem_aware", "solution_aware", "proposal_aware", "most_aware"}
_OBJ_ID_RE = re.compile(r"^OBJ\d+$")
_SINGLE = ("decision_maker", "ask", "want", "current_belief", "status_quo", "stakes", "reframe", "trust")
_LISTS = ("stakeholders", "belief_bridge", "objections", "limits_to_admit")
# PER-28: an ask must be answerable yes or no. These verbs ask for attention, not a decision.
VAGUE_VERBS = {"consider", "discuss", "review", "explore", "learn", "note", "understand", "think", "look",
               "see", "hear", "share", "align", "continue discussing", "evaluate", "assess", "reflect", "keep"}


def brief_elements(brief: dict):
    """(path, element) for every element carrying evidence and status."""
    for k in _SINGLE:
        if isinstance(brief.get(k), dict):
            yield k, brief[k]
    for k in _LISTS:
        for i, el in enumerate(brief.get(k) or []):
            yield f"{k}[{i}]", el


def validate_brief(brief: dict, known_evidence_ids: set[str], known_flag_ids: set[str] | None = None) -> list[str]:
    """Structural errors only. Whether the brief persuades is QA's job."""
    errors = []
    missing = [k for k in (*_SINGLE, *_LISTS) if k not in brief]
    if missing:
        return [f"persuasion_brief missing {missing}"]
    for path, el in brief_elements(brief):
        st = el.get("status")
        if st not in BRIEF_STATUSES:
            errors.append(f"persuasion_brief.{path}: unknown status {st!r}")
        for eid in el.get("evidence", []):
            if eid not in known_evidence_ids:
                errors.append(f"persuasion_brief.{path} references unknown evidence {eid!r}")
        if st == "supported" and not el.get("evidence"):
            errors.append(f"persuasion_brief.{path} is 'supported' but cites no evidence")
    stage = brief["current_belief"].get("awareness_stage")
    if stage not in AWARENESS_STAGES:
        errors.append(f"persuasion_brief.current_belief: unknown awareness stage {stage!r}")
    if not brief["ask"].get("statement", "").strip():
        errors.append("persuasion_brief.ask is empty")
    obj_ids = [o.get("id", "") for o in brief["objections"]]
    if len(obj_ids) != len(set(obj_ids)):
        errors.append(f"duplicate objection ids: {obj_ids}")
    for o in brief["objections"]:
        if not _OBJ_ID_RE.match(o.get("id", "")):
            errors.append(f"bad objection id: {o.get('id')!r}")
    ranks = sorted(o.get("rank", 0) for o in brief["objections"])
    if ranks != list(range(1, len(ranks) + 1)):
        errors.append(f"objection ranks must run 1..{len(ranks)}, got {ranks}")
    if known_flag_ids is not None:
        for i, lim in enumerate(brief["limits_to_admit"]):
            if lim.get("linked_flag") and lim["linked_flag"] not in known_flag_ids:
                errors.append(f"persuasion_brief.limits_to_admit[{i}] references unknown flag {lim['linked_flag']!r}")
    if not brief["belief_bridge"]:
        errors.append("persuasion_brief.belief_bridge is empty")
    return errors


def validate_persuasion_links(data: dict) -> list[str]:
    """Errors for references a persuasion-first narrative makes into its brief and frame."""
    errors = []
    brief = data.get("persuasion_brief") or {}
    obj_ids = {o["id"] for o in brief.get("objections", [])}
    steps = {s.get("step") for s in brief.get("belief_bridge", [])}
    for a in data["arguments"]:
        ao = a.get("answers_objection") or ""
        if ao and ao not in obj_ids:
            errors.append(f"argument {a.get('id')} answers unknown objection {ao!r}")
        bs = a.get("bridge_step", 0)
        if bs and bs not in steps:
            errors.append(f"argument {a.get('id')} names unknown bridge step {bs}")
    arg_ids = {a.get("id") for a in data["arguments"]}
    for h in data.get("headline_flow") or []:
        if h.get("argument") not in arg_ids:
            errors.append(f"headline_flow names unknown argument {h.get('argument')!r}")
    frame = data.get("deck_frame") or {}
    after = frame.get("objections_after") or ""
    if after and after not in {s.get("id") for s in frame.get("sections", [])}:
        errors.append(f"deck_frame.objections_after names unknown section {after!r}")
    return errors


_NUM_RE = re.compile(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    """Numeric tokens, normalised (thousands separators removed, trailing .0 dropped)."""
    out = set()
    for m in _NUM_RE.findall(text or ""):
        n = m.replace(",", "").rstrip(".")
        if "." in n:
            n = n.rstrip("0").rstrip(".")
        out.add(n)
    return out


def _flatten(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def untraced_numbers(text: str, sources: list) -> set[str]:
    """Numbers in text that appear in none of the sources (evidence items, profile).
    Single digits are ignored: counts like "three steps" or list ordinals are not figures."""
    allowed = set()
    for s in sources:
        allowed |= numbers_in(_flatten(s))
    return {n for n in numbers_in(text) if n not in allowed and not (len(n) == 1 and n.isdigit())}


def ask_verb_ok(ask: str) -> tuple[bool, str]:
    words = re.findall(r"[A-Za-z']+", (ask or "").lower())
    if not words:
        return False, "the ask is empty"
    first = words[0]
    if first in ("please", "we", "i", "let's", "lets", "to"):
        first = words[1] if len(words) > 1 else ""
    if first in VAGUE_VERBS:
        return False, f"the ask starts with {first!r}, which asks for attention, not a decision"
    return True, first


def persuasion_findings(narrative: dict, evidence: list[dict], profile: dict) -> list[dict]:
    """Deterministic persuasion checks on a persuasion-first narrative, shaped like
    narrative QA findings. Model judgment on the same rules is in qa_narrative.md."""
    brief = narrative.get("persuasion_brief")
    if not brief:
        return [{"rule": "PER-10", "field": "persuasion_brief", "severity": "block",
                 "problem": "A persuasion-first narrative has no persuasion brief.",
                 "fix": "Revise the narrative so the brief is produced first."}]
    out = []
    ev_by_id = {e["id"]: e for e in evidence}

    def add(rule, field, severity, problem, fix):
        out.append({"rule": rule, "field": field, "severity": severity, "problem": problem, "fix": fix})

    # PER-34: a figure in the brief must come from evidence or the profile. One found
    # in no evidence at all may be invented (block). One found only in evidence the
    # element does not cite is a citation gap (warn, naming where it was found).
    for path, el in brief_elements(brief):
        texts = " ".join(str(v) for k, v in el.items() if isinstance(v, str) and k not in ("status", "id"))
        cited = [ev_by_id[e] for e in el.get("evidence", []) if e in ev_by_id]
        uncited = untraced_numbers(texts, cited + [profile])
        invented = untraced_numbers(texts, evidence + [profile]) & uncited
        if invented:
            add("PER-34", f"persuasion_brief.{path}", "block",
                f"States {', '.join(sorted(invented))}, which appear in no evidence item or the profile.",
                "Remove the figure and mark the element open, or cite the evidence it comes from.")
        gap = uncited - invented
        if gap:
            where = sorted({e["id"] for e in evidence for n in gap if n in numbers_in(_flatten(e))})
            add("PER-34", f"persuasion_brief.{path}", "warn",
                f"States {', '.join(sorted(gap))} without citing the evidence it comes from ({', '.join(where)}).",
                "Add the evidence id to the element's evidence.")
    # PER-05, PER-07, PER-21: the brief has a want, stakes, and a reframe.
    if not brief["want"].get("internal", "").strip():
        add("PER-06", "persuasion_brief.want", "warn", "The brief names no internal problem.",
            "State how the decision feels to the person accountable for it.")
    for key, rule in (("stakes", "PER-07"), ("reframe", "PER-21")):
        if brief[key].get("status") == "open":
            add(rule, f"persuasion_brief.{key}", "warn",
                f"The brief's {key} is open: the evidence does not establish it.",
                f"Find evidence for the {key} or build the case without it.")
    # PER-16: every admitted limit is real.
    for i, lim in enumerate(brief["limits_to_admit"]):
        if not lim.get("evidence") and not lim.get("linked_flag"):
            add("PER-16", f"persuasion_brief.limits_to_admit[{i}]", "block",
                "An admitted limit cites no evidence and no flag, so it may be a token flaw.",
                "Tie the limit to the evidence or flag it comes from, or drop it.")
    if not brief["limits_to_admit"]:
        add("PER-15", "persuasion_brief.limits_to_admit", "warn", "The brief admits no limit.",
            "Name the strongest real limit of the case so the deck can state it.")
    # PER-10: the bridge starts from a held belief and every later step is argued.
    bridge = sorted(brief["belief_bridge"], key=lambda s: s.get("step", 0))
    if bridge and not bridge[0].get("already_held"):
        add("PER-10", "persuasion_brief.belief_bridge", "warn",
            "The belief bridge does not start from a belief the audience already holds.",
            "Start the bridge from something the audience already accepts.")
    argued = {a.get("bridge_step") for a in narrative["arguments"]}
    for s in bridge[1:]:
        if s.get("step") not in argued:
            add("PER-10", f"belief_bridge.step{s.get('step')}", "block",
                f"Bridge step {s.get('step')} ({s.get('belief', '')[:80]}) has no argument, so the audience must jump it.",
                "Add an argument that moves this belief, citing its evidence.")
    # PER-41: every main argument moves a bridge belief.
    for a in narrative["arguments"]:
        if a["role"] in MAIN_ROLES and not a.get("bridge_step"):
            add("PER-41", f"argument:{a['id']}", "warn",
                f"{a['id']} moves no belief on the bridge.",
                "Map it to a bridge step or move it out of the main deck.")
        if a["role"] in MAIN_ROLES and not a.get("evidence"):
            add("PER-02", f"argument:{a['id']}", "block", f"{a['id']} cites no evidence.",
                "Cite the evidence that proves this step, or mark it as an unresolved question.")
    # PER-13: the biggest objection is answered early.
    frame = narrative.get("deck_frame") or {}
    sections = frame.get("sections") or []
    after = frame.get("objections_after") or ""
    if brief["objections"] and sections:
        if not after:
            add("PER-13", "deck_frame.objections_after", "warn",
                "The frame does not say where the objections are answered.",
                "Name the section after which the ranked objections are answered.")
        else:
            ids = [s["id"] for s in sections]
            before = sum(len(s.get("arguments", [])) for s in sections[:ids.index(after) + 1])
            total = sum(len(s.get("arguments", [])) for s in sections)
            if total and before > total / 2:
                add("PER-13", "deck_frame.objections_after", "warn",
                    f"Objections are answered after {before} of {total} argument slides, past the halfway point.",
                    "Answer the biggest objection earlier.")
    out += story_findings(narrative, evidence, profile)
    # PER-28, PER-37: the closing ask is the brief's direct ask.
    ask = (frame.get("closing") or {}).get("ask", "")
    ok, verb = ask_verb_ok(ask)
    if not ok:
        add("PER-28", "deck_frame.closing.ask", "block", f"The closing ask is not a direct request: {verb}.",
            "Start the ask with the concrete verb from the brief.")
    brief_verb = (brief["ask"].get("verb") or "").lower().strip()
    if ok and brief_verb and brief_verb not in ask.lower():
        add("PER-37", "deck_frame.closing.ask", "warn",
            f"The closing ask does not use the brief's verb {brief_verb!r}.",
            "Make the closing ask the brief's ask.")
    ok, why = ask_verb_ok(brief["ask"].get("statement", ""))
    if not ok:
        add("PER-28", "persuasion_brief.ask", "block", f"The brief's ask is not a direct request: {why}.",
            "Write the ask as a yes-or-no request starting with a concrete verb.")
    return out


HEADLINE_MAX = 59  # one line on the headline-and-subhead layouts (template/build_manifest.py)


def slide_arguments(narrative: dict) -> list[str]:
    """Argument ids that get their own claim slide, in deck order (the planner's rule)."""
    from ..compose.planner import _placed_arguments
    return [a["id"] for a, _ in _placed_arguments(narrative)[0]]


def story_findings(narrative: dict, evidence: list[dict], profile: dict) -> list[dict]:
    """PER-45 and PER-46: the story exists, and the headline flow gives every slide one
    short selling headline that invents no figure."""
    out = []

    def add(rule, field, severity, problem, fix):
        out.append({"rule": rule, "field": field, "severity": severity, "problem": problem, "fix": fix})

    story = narrative.get("story") or {}
    missing = [k for k in ("problem", "why_care", "solution", "why_best", "big_idea") if not (story.get(k) or "").strip()]
    if missing:
        add("PER-45", "story", "block", f"The story is missing: {', '.join(missing)}.",
            "Write the problem, why it matters to these people, the solution, why it is best, and the big idea.")
    ev_by_id = {e["id"]: e for e in evidence}
    if story:
        text = " ".join(str(story.get(k, "")) for k in ("problem", "why_care", "solution", "why_best", "big_idea"))
        cited = [ev_by_id[e] for e in story.get("evidence", []) if e in ev_by_id]
        invented = untraced_numbers(text, evidence + [profile])
        gap = untraced_numbers(text, cited + [profile]) - invented
        if invented:
            add("PER-34", "story", "block", f"The story states {', '.join(sorted(invented))}, found in no evidence item.",
                "Remove the figure or cite the evidence it comes from.")
        if gap:
            add("PER-34", "story", "warn", f"The story states {', '.join(sorted(gap))} without citing its evidence.",
                "Add the evidence id to the story's evidence.")
    flow = narrative.get("headline_flow") or []
    by_arg: dict[str, list[dict]] = {}
    for h in flow:
        by_arg.setdefault(h.get("argument"), []).append(h)
    args = {a["id"]: a for a in narrative["arguments"]}
    order = slide_arguments(narrative)
    for aid in order:
        hs = by_arg.get(aid, [])
        if len(hs) != 1:
            add("PER-46", f"headline_flow:{aid}", "block",
                f"{aid} has {len(hs)} headlines in the flow; its slide needs exactly one.",
                "Give every slide's argument one selling headline.")
    seen = set()
    for h in flow:
        head, aid = (h.get("headline") or "").strip(), h.get("argument")
        if len(head) > HEADLINE_MAX:
            add("PER-46", f"headline_flow:{aid}", "block",
                f"Headline is {len(head)} characters, more than the {HEADLINE_MAX} one line holds: {head!r}.",
                "Shorten it. The detail belongs in the subhead.")
        ev = [ev_by_id[e] for e in args.get(aid, {}).get("evidence", []) if e in ev_by_id]
        invented = untraced_numbers(head, evidence + [profile])
        gap = untraced_numbers(head, ev + [profile]) - invented
        if invented:
            add("PER-34", f"headline_flow:{aid}", "block",
                f"Headline states {', '.join(sorted(invented))}, found in no evidence item.", "Remove the figure.")
        if gap:
            add("PER-34", f"headline_flow:{aid}", "warn",
                f"Headline states {', '.join(sorted(gap))}, which is not in {aid}'s evidence.",
                "Remove the figure or cite the evidence in the argument.")
        if head.lower() in seen:
            add("PER-46", f"headline_flow:{aid}", "warn", f"Headline repeats another: {head!r}.", "Make each headline distinct.")
        seen.add(head.lower())
    flow_order = [h.get("argument") for h in flow if h.get("argument") in order]
    if flow_order and flow_order != [a for a in order if a in flow_order]:
        add("PER-46", "headline_flow", "warn", "The headline flow is not in deck order, so it does not read as the deck will.",
            "List the headlines in section order.")
    return out
