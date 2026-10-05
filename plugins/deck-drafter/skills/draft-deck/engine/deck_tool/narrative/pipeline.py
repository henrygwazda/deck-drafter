"""
Milestone 4 Phase 2 orchestration: develop a narrative proposal from Phase 1's
ingested evidence, run QA against it, and gate human approval -- a dedicated CLI
surface, not decisions.interactive_review(), since approving a whole narrative_spec
isn't a single rule-keyed outcome and "revise conversationally" needs free-text
feedback that mechanism has no slot for.
"""
from __future__ import annotations

import time

from .. import decisions, persuasion
from ..common import load_json, run_dir, save_json
from .develop import develop_brief, develop_narrative
from .kb_rules import narrative_rule_ids, narrative_rules
from .qa import qa_narrative
from .validate import brief_elements, validate_brief, validate_narrative_spec

_PROFILE_KEYS = ("deck_type", "purpose", "audience", "delivery", "channels")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _narrow_profile(profile: dict) -> dict:
    return {k: profile[k] for k in _PROFILE_KEYS if k in profile}


def _snapshot(narrative: dict) -> dict:
    return {k: v for k, v in narrative.items() if k != "version_history"}


def _known_evidence_ids(ingested: dict) -> set[str]:
    return {e["id"] for e in ingested["evidence"]}


def _require(run, name: str, hint: str) -> dict:
    data = load_json(run / name)
    if not data:
        raise SystemExit(hint)
    return data


def _load_stage_inputs(deck_id: str):
    run = run_dir(deck_id)
    ingested = _require(run, "ingested_spec.json",
                         f"No ingested spec for {deck_id}. Run: python3 deck.py ingest <profile> <source_dir>")
    active = _require(run, "active_rules.json",
                       f"No activation for {deck_id}. Run: python3 deck.py activate <profile>")
    return run, ingested, active


def _log_excluded_evidence(run, narrative: dict):
    """Narrative-level editorial decisions: evidence the model deliberately chose not to
    use anywhere in the narrative. review_required=True -- this is the model actively
    dropping real evidence, the same category of judgment CLAUDE.md says humans must
    keep authority over (mirrors ingestion's flag_contradiction precedent)."""
    decisions.clear_stage(run, "narrative_editorial")
    for ex in narrative["excluded_evidence"]:
        decisions.log(run, stage="narrative_editorial", source="MODEL", action="exclude_evidence",
                       target={"type": "evidence", "id": ex.get("evidence")}, result=ex.get("reason", ""),
                       review_required=True)


def _log_brief(run, narrative: dict):
    """Brief elements the evidence cannot support are open questions a human should see,
    not gaps the model filled (CLAUDE.md: never invent evidence)."""
    decisions.clear_stage(run, "narrative_persuasion")
    brief = narrative.get("persuasion_brief")
    if not brief:
        return
    for path, el in brief_elements(brief):
        if el.get("status") == "open":
            text = next((v for k, v in el.items() if isinstance(v, str) and k not in ("status", "id", "verb")), "")
            decisions.log(run, stage="narrative_persuasion", source="MODEL", action="brief_open",
                          target={"type": "brief", "id": path},
                          result=f"The evidence does not establish this brief element: {text[:200]}",
                          kb_rules=["PER-34"], review_required=True)


def _write_reports(run, narrative: dict, qa_findings: list[dict]):
    lines = [f"# Narrative report: {narrative['deck_id']} (v{narrative['version']})", "",
             f"Status: {narrative['approval']['status']}. Method: {narrative.get('method', 'evidence')}.", ""]
    if narrative.get("persuasion_brief"):
        lines += _brief_lines(narrative["persuasion_brief"])
    lines += [f"**Thesis**: {narrative['thesis']}", "", "## Arguments", ""]
    for a in narrative["arguments"]:
        extra = ""
        if a.get("bridge_step"):
            extra += f" [bridge step {a['bridge_step']}]"
        if a.get("answers_objection"):
            extra += f" [answers {a['answers_objection']}]"
        lines.append(f"- **{a['id']}** ({a['role']}){extra}: {a['statement']} "
                      f"— evidence: {', '.join(a['evidence']) or 'none'}")
        if a.get("moves_belief"):
            lines.append(f"  Moves: {a['moves_belief']}")
    if narrative["unresolved_questions"]:
        lines += ["", "## Unresolved questions", ""]
        for uq in narrative["unresolved_questions"]:
            lines.append(f"- **{uq['id']}** (flag {uq.get('linked_flag', '-')}): {uq['note']} "
                          f"— evidence: {', '.join(uq['evidence'])}")
    if narrative["narrative_progression"]:
        lines += ["", "## Progression", ""]
        for step in narrative["narrative_progression"]:
            lines.append(f"{step['step']}. **{step['label']}**: {step['description']} "
                          f"({', '.join(step['arguments']) or 'no arguments yet'})")
    frame = narrative.get("deck_frame")
    if frame:
        lines += ["", "## Deck frame", "",
                  f"**Title**: {frame['title'].get('headline', '')} ({frame['title'].get('subhead', '')})", ""]
        for sec in frame.get("sections", []):
            lines.append(f"- **{sec['id']} {sec['title']}**: {sec.get('question', '')} "
                         f"({', '.join(sec.get('arguments', []))}). {sec.get('summary', '')}")
        if frame.get("sections_rationale"):
            lines += ["", f"Sections: {frame['sections_rationale']}"]
        if frame.get("objections_after"):
            lines += ["", f"Objections answered after {frame['objections_after']}."]
        lines += ["", f"**Closing**: {frame['closing'].get('headline', '')}. Ask: {frame['closing'].get('ask', '')}"]
    if narrative.get("story"):
        lines += _story_lines(narrative)
    (run / "narrative_report.md").write_text("\n".join(lines) + "\n")

    qa_lines = [f"# Narrative QA report: {narrative['deck_id']} (v{narrative['version']})", "",
                f"{len(qa_findings)} finding(s).", ""]
    if qa_findings:
        qa_lines += ["| Severity | Rule | Field | Problem | Fix |", "|---|---|---|---|---|"]
        qa_lines += [f"| {f['severity']} | {f['rule']} | {f['field']} | {f['problem']} | {f['fix']} |"
                     for f in qa_findings]
    (run / "narrative_qa_report.md").write_text("\n".join(qa_lines) + "\n")


def _ev(el: dict) -> str:
    return f" ({el.get('status')}{': ' + ', '.join(el['evidence']) if el.get('evidence') else ''})"


def _brief_lines(b: dict) -> list[str]:
    """The persuasion brief as a reader sees it, with each element's status and evidence."""
    L = ["## Persuasion brief", "",
         f"- **Decision-maker**: {b['decision_maker'].get('role', '')}, accountable for "
         f"{b['decision_maker'].get('accountable_for', '')}{_ev(b['decision_maker'])}"]
    for st in b.get("stakeholders", []):
        L.append(f"- **Stakeholder**: {st.get('role', '')}, accountable for {st.get('accountable_for', '')}, "
                 f"judges on {st.get('judges_on', '')}{_ev(st)}")
    L += [f"- **Ask**: {b['ask'].get('statement', '')}{_ev(b['ask'])}",
          f"- **Want**: external: {b['want'].get('external', '')} Internal: {b['want'].get('internal', '')}"
          + (f" Philosophical: {b['want']['philosophical']}" if b['want'].get('philosophical') else "") + _ev(b['want']),
          f"- **Believes now** ({b['current_belief'].get('awareness_stage', '')}): "
          f"{b['current_belief'].get('belief', '')}{_ev(b['current_belief'])}",
          f"- **Status quo**: {b['status_quo'].get('alternative', '')} Cost: {b['status_quo'].get('cost', '')}"
          f"{_ev(b['status_quo'])}",
          f"- **Stakes**: {b['stakes'].get('statement', '')}{_ev(b['stakes'])}",
          f"- **Reframe**: {b['reframe'].get('statement', '')}{_ev(b['reframe'])}",
          f"- **Trust**: empathy: {b['trust'].get('empathy', '')} Competence: {b['trust'].get('competence', '')}"
          f"{_ev(b['trust'])}", "", "Belief bridge:", ""]
    for s in sorted(b.get("belief_bridge", []), key=lambda x: x.get("step", 0)):
        L.append(f"{s.get('step')}. {s.get('belief', '')}{' (already held)' if s.get('already_held') else ''}{_ev(s)}")
    L += ["", "Objections, ranked:", ""]
    for o in sorted(b.get("objections", []), key=lambda x: x.get("rank", 0)):
        L.append(f"{o.get('rank')}. **{o.get('id')}** ({o.get('held_by', '')}): {o.get('objection', '')} "
                 f"Answer: {o.get('answer', '')}{_ev(o)}")
    L += ["", "Limits to admit:", ""]
    for lim in b.get("limits_to_admit", []):
        L.append(f"- {lim.get('limit', '')} Why: {lim.get('why_credible', '')}"
                 f"{' Flag ' + lim['linked_flag'] + '.' if lim.get('linked_flag') else ''}{_ev(lim)}")
    if b.get("open_questions"):
        L += ["", "Open in the brief:", ""]
        L += [f"- {q.get('element', '')}: {q.get('note', '')}" for q in b["open_questions"]]
    return L + [""]


def _story_lines(n: dict) -> list[str]:
    """The end of the report: the story, then the deck as its headlines read in order."""
    st, frame = n["story"], n.get("deck_frame") or {}
    L = ["", "## The story", "",
         f"**The problem.** {st.get('problem', '')}", "",
         f"**Why they will care.** {st.get('why_care', '')}", "",
         f"**Our answer.** {st.get('solution', '')}", "",
         f"**Why it is the best way forward.** {st.get('why_best', '')}", "",
         f"**In one sentence.** {st.get('big_idea', '')}", "",
         "## The deck as its headlines", "",
         "Read in order, these are the slides. Each claim slide carries its evidence as a subhead beneath the headline.", ""]
    heads = {h["argument"]: h for h in n.get("headline_flow") or []}
    ask = ((n.get("persuasion_brief") or {}).get("ask") or {}).get("statement", "")
    k = 1
    L.append(f"{k}. **{frame.get('title', {}).get('headline', '')}** (title)")
    k += 1
    if ask:
        L.append(f"{k}. **{ask}** (executive summary: the ask and three reasons)")
        k += 1
    from .validate import slide_arguments
    sec_of = {a: sec for sec in frame.get("sections", []) for a in sec.get("arguments", [])}
    shown, after = set(), frame.get("objections_after") or ""
    order = slide_arguments(n)
    for i, aid in enumerate(order):
        sec = sec_of.get(aid)
        if sec and sec["id"] not in shown:
            shown.add(sec["id"])
            L.append(f"   *Section: {sec['title']}*")
        h = heads.get(aid, {})
        L.append(f"{k}. **{h.get('headline', '(no headline)')}** ({h.get('job', '')}, {aid})")
        k += 1
        nxt = order[i + 1] if i + 1 < len(order) else None
        if after and sec and sec["id"] == after and (nxt is None or sec_of.get(nxt, {}).get("id") != after):
            L.append(f"{k}. **Objections answered** (ranked objections and their answers)")
            k += 1
    if not after:
        L.append(f"{k}. **Objections answered** (ranked objections and their answers)")
        k += 1
    L.append(f"{k}. **{frame.get('closing', {}).get('headline', '')}** (closing). Ask: {frame.get('closing', {}).get('ask', '')}")
    return L


def _develop_candidate(method, evidence, sources, flags, profile, rules, prior=None, feedback=None):
    """(brief or None, narrative candidate). In persuasion mode the brief comes first and the
    narrative is built on it; a revision revises both from the same feedback."""
    brief = None
    if method == "persuasion":
        brief = develop_brief(evidence, sources, flags, profile,
                              prior=(prior or {}).get("persuasion_brief"), feedback=feedback)
        known_ev, known_flags = {e["id"] for e in evidence}, {f.get("id") for f in flags}
        errors = validate_brief(brief, known_ev, known_flags)
        if errors:
            # One corrective retry with the validator's errors (e.g. a flag id placed in an
            # evidence list). References are never silently stripped.
            fix = ("The brief failed validation. Return the same brief with only these errors fixed; flag ids "
                   "(FLAG..) belong in linked_flag or in the text, never in an evidence list:\n"
                   + "\n".join(f"- {e}" for e in errors))
            brief = develop_brief(evidence, sources, flags, profile, prior=brief, feedback=fix)
            errors = validate_brief(brief, known_ev, known_flags)
        if errors:
            raise SystemExit("Generated persuasion brief failed validation:\n" + "\n".join(f"  {e}" for e in errors))
    prior_body = {k: v for k, v in (prior or {}).items() if k != "persuasion_brief"} or None
    candidate = develop_narrative(evidence, sources, flags, profile, rules, prior=prior_body, feedback=feedback,
                                  method=method, brief=brief)
    return brief, candidate


def _considered(active: dict, method: str) -> list[str]:
    ids = narrative_rule_ids(active)
    if method == "persuasion":
        ids += sorted(persuasion.rule_lines("persuasion_brief", "narrative", "frame"))
    return ids


def develop(deck_id: str, quick_draft: bool = False) -> dict:
    run, ingested, active = _load_stage_inputs(deck_id)
    evidence, sources, flags = ingested["evidence"], ingested["sources"], ingested["flags"]
    profile = _narrow_profile(active["profile"])
    rules = narrative_rules(active, evidence)
    method = persuasion.method(None)

    brief, candidate = _develop_candidate(method, evidence, sources, flags, profile, rules)
    approval_status = "quick_draft_accepted" if quick_draft else "pending"

    narrative = {
        "schema_version": "narrative_spec/v1", "deck_id": deck_id, "version": 1, "method": method,
        "status": approval_status,
        "approval": {"status": approval_status, "approved_at": None, "last_approved_version": None},
        "audience_purpose": {
            "intended_audience": active["profile"].get("audience", {}).get("primary", ""),
            "presentation_purpose": active["profile"].get("purpose", ""),
            "desired_understanding_or_decision": active["profile"].get("purpose", ""),
        },
        "thesis": candidate["thesis"], "arguments": candidate["arguments"],
        "unresolved_questions": candidate["unresolved_questions"],
        "narrative_progression": candidate["narrative_progression"],
        "excluded_evidence": candidate["excluded_evidence"],
        "deck_frame": candidate.get("deck_frame"),
        **{k: candidate[k] for k in ("story", "headline_flow") if k in candidate},
        "kb_rules_considered": _considered(active, method),
        "version_history": [], "last_qa": None,
    }
    if brief is not None:
        narrative["persuasion_brief"] = brief
    errors = validate_narrative_spec(narrative, _known_evidence_ids(ingested))
    if errors:
        raise SystemExit("Generated narrative_spec failed validation:\n" + "\n".join(f"  {e}" for e in errors))

    narrative["version_history"] = [{"version": 1, "ts": _now(), "source": "MODEL",
                                      "change": "initial draft", "feedback": None,
                                      "snapshot": _snapshot(narrative)}]

    findings = qa_narrative(narrative, evidence, flags, profile, rules, active)
    narrative["last_qa"] = {"ts": _now(), "findings": findings}

    save_json(run / "narrative_spec.json", narrative)
    _log_excluded_evidence(run, narrative)
    _log_brief(run, narrative)
    _write_reports(run, narrative, findings)
    return narrative


def qa(deck_id: str) -> list[dict]:
    run, ingested, active = _load_stage_inputs(deck_id)
    narrative = _require(run, "narrative_spec.json",
                          f"No narrative for {deck_id}. Run: python3 deck.py narrative develop {deck_id}")
    profile = _narrow_profile(active["profile"])
    rules = narrative_rules(active, ingested["evidence"])
    findings = qa_narrative(narrative, ingested["evidence"], ingested["flags"], profile, rules, active)
    narrative["last_qa"] = {"ts": _now(), "findings": findings}
    save_json(run / "narrative_spec.json", narrative)
    _write_reports(run, narrative, findings)
    return findings


def approve(deck_id: str, force: bool = False) -> dict:
    run = run_dir(deck_id)
    narrative = _require(run, "narrative_spec.json",
                          f"No narrative for {deck_id}. Run: python3 deck.py narrative develop {deck_id}")
    last_qa = narrative.get("last_qa")
    if not last_qa:
        raise SystemExit(f"No QA report for {deck_id}. Run: python3 deck.py narrative qa {deck_id}")
    blocking = [f for f in last_qa["findings"] if f.get("severity") == "block"]
    if blocking and not force:
        lines = "\n".join(f"  {f['rule']} ({f['field']}): {f['problem']}" for f in blocking)
        raise SystemExit(f"{len(blocking)} blocking finding(s) unresolved:\n{lines}\n"
                          f"Re-run with --force to approve anyway.")
    narrative["approval"] = {"status": "approved", "approved_at": _now(), "last_approved_version": narrative["version"]}
    narrative["status"] = "approved"
    save_json(run / "narrative_spec.json", narrative)
    return narrative


def qa_feedback(deck_id: str) -> str:
    """The last QA report's findings as revision feedback, so a revision driven by QA is
    reproducible and identical in form whichever narrative method produced the draft."""
    narrative = _require(run_dir(deck_id), "narrative_spec.json", f"No narrative for {deck_id}.")
    findings = (narrative.get("last_qa") or {}).get("findings", [])
    if not findings:
        raise SystemExit(f"No QA findings to revise from for {deck_id}.")
    lines = ["Revise the narrative to resolve each of these narrative QA findings, without stating "
             "anything the evidence does not support:"]
    lines += [f"- [{f['severity']}] {f['rule']} ({f['field']}): {f['problem']} Fix: {f['fix']}" for f in findings]
    return "\n".join(lines)


def revise(deck_id: str, feedback: str) -> dict:
    run, ingested, active = _load_stage_inputs(deck_id)
    narrative = _require(run, "narrative_spec.json",
                          f"No narrative for {deck_id}. Run: python3 deck.py narrative develop {deck_id}")
    evidence, sources, flags = ingested["evidence"], ingested["sources"], ingested["flags"]
    profile = _narrow_profile(active["profile"])
    rules = narrative_rules(active, evidence)

    prior_body = _snapshot(narrative)
    method = persuasion.method(narrative)
    brief, candidate = _develop_candidate(method, evidence, sources, flags, profile, rules,
                                          prior=prior_body, feedback=feedback)

    new_version = narrative["version"] + 1
    updated = dict(narrative)
    updated.update({
        "version": new_version, "status": "pending",
        "thesis": candidate["thesis"], "arguments": candidate["arguments"],
        "unresolved_questions": candidate["unresolved_questions"],
        "narrative_progression": candidate["narrative_progression"],
        "excluded_evidence": candidate["excluded_evidence"],
        "deck_frame": candidate.get("deck_frame"),
        **{k: candidate[k] for k in ("story", "headline_flow") if k in candidate},
        "approval": {"status": "pending", "approved_at": None,
                     "last_approved_version": narrative["approval"].get("last_approved_version")},
        "method": method,
    })
    if brief is not None:
        updated["persuasion_brief"] = brief
    errors = validate_narrative_spec(updated, _known_evidence_ids(ingested))
    if errors:
        raise SystemExit("Revised narrative_spec failed validation:\n" + "\n".join(f"  {e}" for e in errors))

    updated["version_history"] = narrative["version_history"] + [{
        "version": new_version, "ts": _now(), "source": "HUMAN", "change": "revision",
        "feedback": feedback, "snapshot": prior_body,
    }]

    findings = qa_narrative(updated, evidence, flags, profile, rules, active)
    updated["last_qa"] = {"ts": _now(), "findings": findings}

    save_json(run / "narrative_spec.json", updated)
    _log_excluded_evidence(run, updated)
    _log_brief(run, updated)
    _write_reports(run, updated, findings)
    return updated
