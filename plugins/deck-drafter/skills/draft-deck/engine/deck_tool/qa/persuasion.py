"""
Persuasion QA (PERSUASION_RUN P5): does the composed deck move its decision-maker to
act, with every claim still resting on cited evidence? Works on any v0.2 deck_spec, so
an evidence-first and a persuasion-first deck are checked the same way.

Three parts, reported separately:
1. Deterministic checks (always run): a direct ask on the closing and executive summary
   (PER-28, PER-29); headlines addressing the audience more than the presenter (PER-03);
   the biggest objection answered before the halfway point (PER-13); every figure in a
   headline, live point, or objection row traced to cited evidence (PER-34, PER-35);
   no hype words (PER-32).
2. A model review written as the profile's decision-maker (PER-42): would it act, what
   stops it, and specific fixes per slide.
3. A claim-by-claim trace (PER-35): each headline, executive summary point, closing
   ask, and objection answer judged supported, overstated, or unsupported against the
   evidence it cites.
"""
from __future__ import annotations

import re

from .. import decisions, persuasion
from ..common import call_json, fill, llm_mode
from ..narrative.validate import ask_verb_ok, untraced_numbers

HYPE = ("innovative", "leading", "unique", "breakthrough", "game-changing", "game changing", "best-in-class",
        "revolutionary", "cutting-edge", "world-class", "unparalleled", "unprecedented", "transformative")
PRESENTER_TERMS = ("we", "our", "us", "helix", "helix bioworks", "the team", "our team")
AUDIENCE_PRONOUNS = ("you", "your", "yours")
CONTENT_LAYOUTS_SKIP = ("title", "section_divider", "closing")
_STOP = {"the", "and", "of", "a", "an", "for", "to", "in", "on", "lab", "group", "team", "director", "contact",
         "partner", "leads", "day-to-day", "user", "decision", "renewal", "operations"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z'-]*", (text or "").lower())


def audience_terms(profile: dict) -> set[str]:
    """Distinctive words naming the audience (organisation names, roles), from the profile."""
    aud = (profile or {}).get("audience", {})
    text = " ".join([aud.get("primary", "")] + list(aud.get("segments", [])))
    return {w for w in _words(text) if len(w) > 3 and w not in _STOP}


def _count(text: str, terms) -> int:
    ws = _words(text)
    joined = " " + " ".join(ws) + " "
    return sum(joined.count(f" {t} ") for t in terms)


def _ev_by_id(spec: dict) -> dict:
    return {e["id"]: e for e in spec.get("evidence", [])}


def _slide_evidence(slide: dict, ev_by_id: dict) -> list[dict]:
    return [ev_by_id[r["ref"]] for r in slide.get("evidence", []) if r.get("ref") in ev_by_id]


def _claim_evidence(spec: dict, slide: dict, ev_by_id: dict) -> list[dict]:
    """A claim slide's own evidence plus the evidence its claim cites."""
    out = _slide_evidence(slide, ev_by_id)
    claim = next((c for c in spec.get("claims", []) if c["id"] == slide.get("claim")), None)
    if claim:
        out += [ev_by_id[e] for e in claim.get("evidence", []) if e in ev_by_id]
    return out


def _deck_cited(spec: dict, ev_by_id: dict) -> list[dict]:
    ids = set()
    for s in spec["slides"]:
        ids |= {r["ref"] for r in s.get("evidence", [])}
    for c in spec.get("claims", []):
        ids |= set(c.get("evidence", []))
    return [ev_by_id[i] for i in sorted(ids) if i in ev_by_id]


def deterministic(spec: dict, profile: dict) -> dict:
    """{"checks": [{"check", "rule", "passed", "detail"}], "untraced": [...]}"""
    ev_by_id = _ev_by_id(spec)
    slides = spec["slides"]
    checks = []

    def add(check, rule, passed, detail):
        checks.append({"check": check, "rule": rule, "passed": bool(passed), "detail": detail})

    closing = next((s for s in reversed(slides) if s["layout"] == "closing"), None)
    ex = next((s for s in slides if s["layout"] == "exec_summary"), None)
    ask = ((closing or {}).get("body", {}).get("live") or [""])[0]
    ok, verb = ask_verb_ok(ask)
    add("closing_ask_direct", "PER-28", ok, f"Closing ask: {ask!r}" + ("" if ok else f" ({verb})"))
    if ex:
        head = ex.get("headline", "")
        stem = verb[:4] if ok and len(verb) >= 4 else verb
        ex_ok, ex_verb = ask_verb_ok(head)
        has_ask = ok and stem and stem in head.lower()
        add("exec_summary_states_ask", "PER-29", has_ask,
            f"Executive summary headline: {head!r}. " + (f"Contains the closing ask's verb {verb!r}." if has_ask
                                                         else f"Does not contain the closing ask's verb {verb!r}."))
    else:
        add("exec_summary_states_ask", "PER-29", False, "No executive summary slide.")

    claims = [s for s in slides if s.get("claim")]
    aud_terms = set(AUDIENCE_PRONOUNS) | audience_terms(profile)
    a = sum(_count(s.get("headline", ""), aud_terms) for s in claims)
    pr = sum(_count(s.get("headline", ""), PRESENTER_TERMS) for s in claims)
    addressed = sum(1 for s in claims if _count(s.get("headline", ""), aud_terms))
    add("headlines_address_audience", "PER-03", a > pr,
        f"Claim headlines: {a} audience references in {addressed} of {len(claims)} headlines, "
        f"{pr} presenter references.")

    content = [s for s in slides if s["layout"] not in CONTENT_LAYOUTS_SKIP]
    risk = [s for s in content if s["layout"] == "alternatives_risks"]
    if risk:
        pos = content.index(risk[0]) + 1
        first = (risk[0].get("risks") or [{}])[0]
        add("biggest_objection_before_half", "PER-13", pos <= len(content) / 2,
            f"First objection or risk row ({first.get('text', '')!r}) is on content slide {pos} of {len(content)}.")
    else:
        add("biggest_objection_before_half", "PER-13", False, "No objections or risks page.")

    untraced = []
    deck_ev = _deck_cited(spec, ev_by_id) + [profile]
    for s in slides:
        if s["layout"] in ("title", "section_divider"):
            continue
        if s.get("claim"):
            pool = _claim_evidence(spec, s, ev_by_id) + [profile]
        else:
            pool = deck_ev
        fields = [("headline", s.get("headline", "")), ("subhead", s.get("subhead", ""))]
        fields += [("live", x) for x in s.get("body", {}).get("live", [])]
        fields += [("risk", f"{r.get('text', '')} {r.get('mitigation', '')}") for r in s.get("risks", [])]
        for field, text in fields:
            bad = untraced_numbers(text, pool)
            if bad:
                anywhere = untraced_numbers(text, list(ev_by_id.values()) + [profile])
                untraced.append({"slide": s["id"], "field": field, "numbers": sorted(bad),
                                 "in_no_evidence": sorted(anywhere), "text": text})
    invented = [u for u in untraced if u["in_no_evidence"]]
    add("figures_trace_to_cited_evidence", "PER-34", not untraced,
        f"{len(untraced)} text field(s) carry a figure not in the evidence their slide cites; "
        f"{len(invented)} of them carry a figure found in no evidence item at all.")
    add("claim_slides_cite_evidence", "PER-02", all(s.get("evidence") for s in claims),
        f"{sum(1 for s in claims if s.get('evidence'))} of {len(claims)} claim slides cite evidence.")

    firsts = [s for s in claims if not s.get("headline", "").endswith("(continued)")]
    with_sub = [s for s in firsts if (s.get("subhead") or "").strip()]
    add("claim_slides_have_subhead", "PER-46", len(with_sub) == len(firsts),
        f"{len(with_sub)} of {len(firsts)} claim slides carry an evidence subhead under the headline.")
    long_heads = [s["id"] for s in with_sub if len(s.get("headline", "")) > 59]
    if with_sub:
        add("selling_headlines_one_line", "PER-46", not long_heads,
            f"Headlines over 59 characters: {', '.join(long_heads) or 'none'}.")

    hype = []
    for s in slides:
        text = " ".join([s.get("headline", "")] + list(s.get("body", {}).get("live", []))).lower()
        hype += [f"{s['id']}: {h}" for h in HYPE if re.search(rf"\b{re.escape(h)}\b", text)]
    add("no_hype_words", "PER-32", not hype, ", ".join(hype) or "None found.")
    return {"checks": checks, "untraced": untraced}


# ---------------- model passes ----------------

def deck_text(spec: dict, include_notes: bool = False, ids: bool = True) -> str:
    """The deck as a reader receives it (read variant): headline, live points, table rows,
    and read prose per slide. ids=False drops slide, objection, and question ids, so a
    blind judge cannot tell which method produced the deck from its labels."""
    secs = {s["id"]: s for s in spec.get("sections", [])}
    out = []
    n = 0
    for s in spec["slides"]:
        n += 1
        label = f"{s['id']} " if ids else ""
        lay = s["layout"].replace("_", " ")
        out.append(f"--- Slide {n} {label}({lay})")
        if s["layout"] == "section_divider":
            sec = secs.get(s.get("section"), {})
            out.append(f"Section: {s.get('headline', '')}. {sec.get('eyebrow', '')} {sec.get('summary', '')}".strip())
            continue
        out.append(f"Headline: {s.get('headline', '')}")
        if s.get("subhead"):
            out.append(f"Subhead: {s['subhead']}")
        for x in s.get("body", {}).get("live", []):
            out.append(f"- {x}")
        for r in s.get("risks", []):
            rid = f"{r.get('id')}: " if ids else ""
            out.append(f"| {rid}{r.get('text', '')} | {r.get('mitigation', '')} |")
        read = s.get("body", {}).get("read", "")
        if read and s["layout"] != "alternatives_risks":
            out.append(f"Text: {read}")
        elif read and ids:
            out.append(f"Text: {read}")
        if include_notes and s.get("notes"):
            out.append(f"Notes: {s['notes']}")
    text = "\n".join(out)
    if not ids:
        text = re.sub(r"\b(OBJ|UQ|ARG|SEC|FLAG|EV|SL|CL)\d+\b:?\s?", "", text)
    return text


def _profile_view(profile: dict) -> dict:
    return {k: profile[k] for k in ("deck_type", "purpose", "audience", "delivery") if k in profile}


def review(spec: dict, profile: dict) -> dict:
    """PER-42: the decision-maker's read of the deck."""
    if llm_mode() == "stub":
        return {"would_act": "conditional", "reason": "Stub review.", "what_stops_me": [], "slide_fixes": [],
                "strongest_slide": "", "weakest_slide": ""}
    return call_json(fill("qa_persuasion.md", PROFILE=_profile_view(profile), DECK=deck_text(spec, include_notes=True),
                          RULES=persuasion.rule_block("slide_copy", "qa")), schema=REVIEW_SCHEMA)


def trace(spec: dict, profile: dict) -> dict:
    """PER-35: each persuasive claim judged against the evidence it cites."""
    ev_by_id = _ev_by_id(spec)
    items = []
    for s in spec["slides"]:
        if s["layout"] in ("title", "section_divider"):
            continue
        if s.get("claim"):
            ev = _claim_evidence(spec, s, ev_by_id)
            claim = s.get("headline", "") + (f" Subhead: {s['subhead']}" if s.get("subhead") else "")
            items.append({"id": f"{s['id']}.headline", "claim": claim, "evidence": sorted({e["id"] for e in ev})})
        elif s["layout"] == "exec_summary":
            items.append({"id": f"{s['id']}.headline", "claim": s.get("headline", ""), "evidence": "deck"})
            items += [{"id": f"{s['id']}.point{i + 1}", "claim": p, "evidence": "deck"}
                      for i, p in enumerate(s.get("body", {}).get("live", []))]
        elif s["layout"] == "closing":
            items.append({"id": f"{s['id']}.headline", "claim": s.get("headline", ""), "evidence": "deck"})
            items += [{"id": f"{s['id']}.ask", "claim": x, "evidence": "deck"} for x in s.get("body", {}).get("live", [])]
        elif s["layout"] == "alternatives_risks":
            items += [{"id": f"{s['id']}.{r.get('id')}", "claim": f"{r.get('text', '')} Answer: {r.get('mitigation', '')}",
                       "evidence": "deck"} for r in s.get("risks", [])]
    if llm_mode() == "stub":
        return {"items": [{"id": it["id"], "verdict": "supported", "reason": "Stub."} for it in items]}
    cited = _deck_cited(spec, ev_by_id)
    return call_json(fill("trace_claims.md", ITEMS=items, EVIDENCE=cited, FLAGS=spec.get("flags", []),
                          PROFILE=_profile_view(profile)), schema=TRACE_SCHEMA)


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_S = {"type": "string"}
REVIEW_SCHEMA = _obj({
    "would_act": {"type": "string", "enum": ["yes", "conditional", "no"]},
    "reason": _S,
    "what_stops_me": {"type": "array", "items": _S},
    "slide_fixes": {"type": "array", "items": _obj({"slide": _S, "rule": _S, "problem": _S, "fix": _S})},
    "strongest_slide": _S, "weakest_slide": _S,
})
TRACE_SCHEMA = _obj({"items": {"type": "array", "items": _obj({
    "id": _S, "verdict": {"type": "string", "enum": ["supported", "overstated", "unsupported"]}, "reason": _S})}})


# ---------------- report ----------------

def run(spec: dict, profile: dict, run_path, model: bool = True) -> dict:
    det = deterministic(spec, profile)
    result = {"deck_id": spec["deck_id"], "deterministic": det}
    if model:
        result["review"] = review(spec, profile)
        result["trace"] = trace(spec, profile)
    decisions.clear_stage(run_path, "persuasion_qa")
    for c in det["checks"]:
        if not c["passed"]:
            decisions.log(run_path, stage="persuasion_qa", source="PERSUASION", action="check_failed",
                          target={"type": "deck", "id": c["check"]}, result=c["detail"], kb_rules=[c["rule"]],
                          review_required=True)
    for f in (result.get("review") or {}).get("slide_fixes", []):
        decisions.log(run_path, stage="persuasion_qa", source="MODEL", action="persuasion_fix",
                      target={"type": "slide", "id": f.get("slide")}, result=f"{f.get('problem')} Fix: {f.get('fix')}",
                      kb_rules=[f.get("rule", "")], review_required=True)
    (run_path / "persuasion_qa_report.md").write_text(report_md(result))
    return result


def trace_counts(tr: dict) -> dict:
    out = {"supported": 0, "overstated": 0, "unsupported": 0}
    for it in tr.get("items", []):
        out[it.get("verdict", "unsupported")] = out.get(it.get("verdict", "unsupported"), 0) + 1
    return out


def report_md(result: dict) -> str:
    det = result["deterministic"]
    L = [f"# Persuasion QA report: {result['deck_id']}", "", "## Deterministic checks", "",
         "| Check | Rule | Result | Detail |", "|---|---|---|---|"]
    for c in det["checks"]:
        L.append(f"| {c['check']} | {c['rule']} | {'pass' if c['passed'] else 'FAIL'} | {c['detail'].replace('|', '/')} |")
    if det["untraced"]:
        L += ["", "Figures not found in the evidence their slide cites:", ""]
        for u in det["untraced"]:
            nowhere = f" ({', '.join(u['in_no_evidence'])} in no evidence item)" if u["in_no_evidence"] else ""
            L.append(f"- {u['slide']} {u['field']}: {', '.join(u['numbers'])}{nowhere}. \"{u['text'][:140]}\"")
    rv = result.get("review")
    if rv:
        L += ["", "## Decision-maker review (model)", "", f"Would act: **{rv.get('would_act')}**. {rv.get('reason', '')}", ""]
        if rv.get("what_stops_me"):
            L += ["What stops me:", ""] + [f"- {x}" for x in rv["what_stops_me"]] + [""]
        L += [f"Strongest slide: {rv.get('strongest_slide', '')}", f"Weakest slide: {rv.get('weakest_slide', '')}", ""]
        if rv.get("slide_fixes"):
            L += ["| Slide | Rule | Problem | Fix |", "|---|---|---|---|"]
            L += [f"| {f.get('slide')} | {f.get('rule')} | {f.get('problem', '').replace('|', '/')} | "
                  f"{f.get('fix', '').replace('|', '/')} |" for f in rv["slide_fixes"]]
    tr = result.get("trace")
    if tr:
        c = trace_counts(tr)
        L += ["", "## Claim-by-claim trace (model)", "",
              f"{c['supported']} supported, {c['overstated']} overstated, {c['unsupported']} unsupported.", ""]
        bad = [it for it in tr.get("items", []) if it.get("verdict") != "supported"]
        if bad:
            L += ["| Item | Verdict | Reason |", "|---|---|---|"]
            L += [f"| {it['id']} | {it['verdict']} | {it.get('reason', '').replace('|', '/')} |" for it in bad]
    return "\n".join(L) + "\n"
