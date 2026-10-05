"""
Milestone 5c slide planning: which slides a deck gets and which layout type each uses,
decided from the approved narrative and the structure of its evidence. No model call.

Order of decisions (brief section 10):
1. Every argument the narrative places gets a slide. Nothing is folded away to save
   space; slide count follows the narrative.
2. A slide's information pattern comes from its evidence taken together (patterns.py).
3. Candidate layout types come from the manifest's pattern fits, strong before
   acceptable. A type is accepted only if it can actually draw the evidence: the
   structured payload it needs is present, and some variant's regions have the
   capacity for it. Variety is left to the renderer, after all of this.
4. Evidence no live layout can hold (a table too long for a slide) goes to the
   appendix with a logged decision, never dropped.
5. Unresolved questions that bear on a claim are attached to that slide as caveats
   (SPW-15: material caveats belong on the slide). The risk register is ranked; the
   highest-ranked items go live, the rest go to the appendix as review-required
   decisions a human can reverse with `deck.py appendix promote`.

Each choice carries its reasons and rules so compose can log it.
"""
from __future__ import annotations

from ..patterns import evidence_pattern
from .layout_map import _usable_kind

# Which pattern anchors a slide when its evidence carries several: the most specific
# structure wins, plain figures and prose come last.
ANCHOR_ORDER = ["scientific_mechanism", "temporal_trend", "relationship", "comparison", "distribution",
                "process_sequence", "evidence_matrix", "mixed_evidence", "quantitative_result",
                "contextual_explanation"]

LIVE_RISK_ROWS = 5  # alternatives_risks.A: three to five rows (NAR-08, NAR-19)
MAX_CAVEATS = 2     # caveats are stated on the slide itself, so they must stay short
RISK_RULES = ["NAR-19", "NAR-59"]
CAVEAT_RULES = ["SPW-15"]


def _chart(e):
    return e.get("chart") or {}


def _diagram(e):
    return e.get("diagram") or {}


# What each claim layout type needs from its primary evidence to draw anything at all.
REQUIRES = {
    "claim_trend": lambda e, q: bool(_chart(e).get("categories")) and _chart(e).get("type") != "scatter",
    "claim_chart": lambda e, q: bool(_chart(e).get("categories")) and _chart(e).get("type") != "scatter",
    "claim_scatter": lambda e, q: _chart(e).get("type") == "scatter",
    "claim_comparison": lambda e, q: bool(e.get("comparison")),
    "claim_diagram": lambda e, q: bool(_diagram(e).get("steps")),
    "claim_mechanism": lambda e, q: bool(_diagram(e).get("nodes") or _diagram(e).get("steps")),
    "claim_table": lambda e, q: bool(e.get("table")),
    "claim_big_number": lambda e, q: e.get("kind") == "big_number",
    "claim_mixed": lambda e, q: e.get("kind") == "big_number" and q is not None,
    "portfolio_funnel": lambda e, q: bool(e.get("stages")),
    "claim_text": lambda e, q: True,
}


def candidate_types(manifest: dict, pattern: str) -> list[tuple[str, str, list[str]]]:
    """[(layout type, fit, rules)] for live claim layouts carrying the pattern, strong first."""
    seen, out = set(), []
    for level in ("strong", "acceptable"):
        for l in manifest["layouts"]:
            f = l["patterns"].get(pattern)
            if (l["family"] != "live" or l["structural"] or l["type"] == "appendix" or not f
                    or f["fit"] != level or l["type"] in seen):
                continue
            seen.add(l["type"])
            out.append((l["type"], level, f["rules"]))
    return out


def _region_capacity_ok(engine, type_, ev) -> tuple[bool, str]:
    """True if at least one live variant of the type has regions that can hold ev."""
    reasons = []
    pseudo = {"evidence": [dict(ev, role="primary", placement="slide")]}
    for lid in engine.by_type.get(type_, []):
        ok = True
        for p in engine.layouts[lid]["placeholders"]:
            if p["role"] == "region":
                fit, why = engine.region_fit(p, pseudo)
                if not fit:
                    ok = False
                    reasons.append(why)
        if ok:
            return True, ""
    return False, "; ".join(reasons)


def _quote_candidate(evs, primary, sources_by_id):
    """A qualitative item from a different source than the figure: what turns a
    figure into mixed quantitative and qualitative evidence."""
    for e in evs:
        if e is primary or e.get("kind") != "text":
            continue
        if e.get("provenance", {}).get("source_id") == primary.get("provenance", {}).get("source_id"):
            continue
        if evidence_pattern(e)[0] in ("mixed_evidence", "contextual_explanation"):
            return e
    return None


def plan_argument(arg: dict, evidence_by_id: dict, engine, sources_by_id=None, taken: set | None = None) -> dict:
    """taken: evidence ids already anchoring another slide, so two slides never show
    the same proof. Updated with this slide's anchor and quote."""
    taken = taken if taken is not None else set()
    evs = [evidence_by_id[eid] for eid in arg.get("evidence", []) if eid in evidence_by_id]
    tagged = {}
    for e in evs:
        pid, how = evidence_pattern(e)
        tagged.setdefault(pid, []).append((e, how))
    rejected, appendix_evidence, appendix_why = [], [], {}
    choice = None

    for pid in ANCHOR_ORDER:
        if choice:
            break
        items = tagged.get(pid, [])
        if pid == "mixed_evidence":
            # A figure plus a quote or observation from another source, whatever the
            # individual tags say.
            items = [(e, h) for p2 in ("quantitative_result", "mixed_evidence") for e, h in tagged.get(p2, [])
                     if e.get("kind") == "big_number"]
        for e, how in items:
            if choice:
                break
            if e["id"] in taken:
                rejected.append(f"{e['id']} already anchors another slide")
                continue
            quote = _quote_candidate([x for x in evs if x["id"] not in taken], e, sources_by_id or {}) \
                if pid == "mixed_evidence" else None
            if pid == "mixed_evidence" and quote is None:
                continue
            for type_, fit, rules in candidate_types(engine.man, pid):
                if not REQUIRES.get(type_, lambda *_: False)(e, quote):
                    rejected.append(f"{type_}: {e['id']} lacks the {type_.split('_', 1)[1]} data it needs")
                    continue
                ok, why = _region_capacity_ok(engine, type_, e)
                if not ok:
                    rejected.append(f"{type_}: {why}")
                    if e.get("kind") == "table" and e not in appendix_evidence:
                        appendix_evidence.append(e)
                        appendix_why[e["id"]] = f"{type_} cannot hold it ({why})"
                    continue
                choice = {"layout": type_, "pattern": pid, "pattern_how": how, "fit": fit, "rules": rules,
                          "primary_evidence": e["id"] if type_ != "claim_text" else None,
                          "quote_evidence": quote["id"] if quote else None}
                break

    if choice is None:
        # Nothing structured could anchor the slide: the claim is carried in words.
        choice = {"layout": "claim_text", "pattern": "contextual_explanation",
                  "pattern_how": "fallback", "fit": "strong", "rules": ["EVD-54"],
                  "primary_evidence": None, "quote_evidence": None}

    taken.update(x for x in (choice["primary_evidence"], choice["quote_evidence"]) if x)
    used = {choice["primary_evidence"], choice["quote_evidence"]} | {e["id"] for e in appendix_evidence}
    # Items whose own structure differs from the slide's: noted so a reviewer can see
    # what the slide does not show, and what a richer layout might carry.
    unshown = sorted({pid for pid, items in tagged.items() for e, _ in items
                      if e["id"] not in used and _usable_kind(e) in ("chart", "comparison", "diagram", "table")}
                     - {choice["pattern"]})
    return {
        "source_type": "argument", "arg_id": arg["id"], "role": arg["role"], **choice,
        "other_evidence": [e["id"] for e in evs if e["id"] not in used],
        "appendix_evidence": [e["id"] for e in appendix_evidence],
        "appendix_why": appendix_why,
        "rejected": rejected, "unshown_patterns": unshown,
    }


def _placed_arguments(narrative: dict) -> tuple[list[tuple[dict, str | None]], list[dict]]:
    """[(argument, section id)] in deck order, plus arguments left for the register.
    With sections, the frame decides: every argument a section lists is a slide, a
    qualification included (SPW-15). Without sections, main arguments are slides in
    narrative order, and each qualification or counterargument follows the main
    argument it shares evidence with, or goes to the register if it shares none."""
    args = {a["id"]: a for a in narrative["arguments"]}
    frame = narrative.get("deck_frame") or {}
    sections = frame.get("sections") or []
    placed, in_section = [], set()
    if sections:
        for sec in sections:
            for aid in sec.get("arguments", []):
                if aid in args and aid not in in_section:
                    placed.append((args[aid], sec["id"]))
                    in_section.add(aid)
        main_left = [a for a in narrative["arguments"] if a["id"] not in in_section and a["role"] in ("primary", "supporting")]
        placed += [(a, None) for a in main_left]
        register = [a for a in narrative["arguments"] if a["id"] not in in_section and a["role"] not in ("primary", "supporting")]
        return placed, register
    main = [a for a in narrative["arguments"] if a["role"] in ("primary", "supporting")]
    others = [a for a in narrative["arguments"] if a["role"] not in ("primary", "supporting")]
    follow = {a["id"]: [] for a in main}
    register = []
    for q in others:
        host = max(main, key=lambda m: len(set(m.get("evidence", [])) & set(q.get("evidence", []))), default=None)
        if host and set(host.get("evidence", [])) & set(q.get("evidence", [])):
            follow[host["id"]].append(q)
        else:
            register.append(q)
    for m in main:
        placed.append((m, None))
        placed += [(q, None) for q in follow[m["id"]]]
    return placed, register


def plan_deck(narrative: dict, evidence_by_id: dict, engine, sources: list[dict] | None = None,
              overrides: dict | None = None) -> dict:
    """Returns {"slides": [...], "risks": {"live": [...], "appendix": [...]}, "appendix": [...]}.

    slides: plan_argument() output plus "section" and "caveats" (unresolved question ids).
    risks: register items {"kind", "id", "evidence", "source", "rank", "why"}.
    appendix: [{"evidence_id", "parent_arg_id", "why"}] for evidence too large for a slide.
    """
    overrides = overrides or {}
    sources_by_id = {s["id"]: s for s in (sources or [])}
    placed, register_args = _placed_arguments(narrative)
    slides, taken = [], set()
    for arg, sec in placed:
        p = plan_argument(arg, evidence_by_id, engine, sources_by_id, taken)
        p["section"] = sec
        p["caveats"] = []
        slides.append(p)

    claim_evidence, shown = {}, {}
    for p in slides:
        for eid in [p["primary_evidence"], p["quote_evidence"], *p["other_evidence"]]:
            if eid:
                claim_evidence.setdefault(eid, []).append(p["arg_id"])
        for eid in (p["primary_evidence"], p["quote_evidence"]):
            if eid:
                shown.setdefault(eid, []).append(p["arg_id"])

    items = [{"kind": "argument", "id": a["id"], "evidence": set(a.get("evidence", [])), "source": a}
             for a in register_args]
    items += [{"kind": "question", "id": q["id"], "evidence": set(q.get("evidence", [])), "source": q}
              for q in narrative.get("unresolved_questions", [])]
    for it in items:
        touched = sorted({aid for eid in it["evidence"] for aid in claim_evidence.get(eid, [])})
        it["shown_on"] = sorted({aid for eid in it["evidence"] for aid in shown.get(eid, [])})
        flag = bool(it["source"].get("linked_flag"))
        score = 3 * flag + 2 * bool(touched) + (1 if it["kind"] == "argument" else 0)
        reasons = [r for r, on in (("linked to an ingestion flag", flag),
                                   (f"bears on {', '.join(touched)}", touched),
                                   ("a counterargument or qualification", it["kind"] == "argument")) if on]
        it.update(rank=score, why="; ".join(reasons) or "not linked to a flag or a claim on a slide")
    # Persuasion-first (PER-13): the brief's ranked objections lead the register, above
    # every other item, so the live page answers what would stop a yes. Unresolved
    # questions still become slide caveats below and keep their own ranking after them.
    brief = narrative.get("persuasion_brief") if narrative.get("method") == "persuasion" else None
    if brief:
        for o in sorted(brief.get("objections", []), key=lambda o: o.get("rank", 0)):
            ev = set(o.get("evidence", []))
            items.append({"kind": "objection", "id": o["id"], "evidence": ev,
                          "source": {"statement": o.get("objection", ""), "answer": o.get("answer", ""),
                                     "held_by": o.get("held_by", ""), "status": o.get("status", "")},
                          "shown_on": sorted({aid for eid in ev for aid in shown.get(eid, [])}),
                          "rank": 100 - o.get("rank", 0),
                          "why": f"brief objection rank {o.get('rank')}, held by {o.get('held_by', 'the audience')}"})
    items.sort(key=lambda it: -it["rank"])  # stable: narrative order breaks ties
    # A question that bears on the evidence a slide actually shows is stated on that
    # slide (SPW-15), highest-ranked first, at most MAX_CAVEATS per slide.
    by_arg = {p["arg_id"]: p for p in slides}
    for it in items:
        if it["kind"] != "question":
            continue
        for aid in it["shown_on"]:
            if len(by_arg[aid]["caveats"]) < MAX_CAVEATS:
                by_arg[aid]["caveats"].append(it["id"])

    promote, demote = set(overrides.get("promote", [])), set(overrides.get("demote", []))
    live, overflow = [], []
    for it in items:
        if it["id"] in demote:
            it["why"] += "; moved to the appendix by a human override"
            overflow.append(it)
        elif it["id"] in promote:
            it["why"] += "; kept live by a human override"
            live.append(it)
        elif len([x for x in live if x["id"] not in promote]) < LIVE_RISK_ROWS:
            live.append(it)
        else:
            overflow.append(it)

    appendix = [{"evidence_id": eid, "parent_arg_id": p["arg_id"], "why": p["appendix_why"][eid]}
                for p in slides for eid in p["appendix_evidence"]]
    return {"slides": slides, "risks": {"live": live, "appendix": overflow}, "appendix": appendix}
