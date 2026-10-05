"""
Milestone 4 Phase 3 orchestration: turn an approved narrative into an actual,
renderable schema-v0.2 spec (sections/claims/slides/appendix), reusing the ingested
sources/evidence registry with dispositions reclassified. Produces a new deck_spec.json
artifact distinct from ingested_spec.json/narrative_spec.json -- each stage's artifact
stays the untouched record of what that stage decided.
"""
from __future__ import annotations

from .. import decisions, persuasion
from ..activate import variants
from ..common import ROOT, load_json, run_dir, save_json
from ..kb import load_brand
from ..spec import validate
from .content import compose_slide_content
from .kb_rules import compose_rules
from .layout_map import assign_layouts

_PRIMARY_RULES = ["CLM-01", "EVD-05"]
_SUPPORTING_RULES = ["EVD-05"]
_FOLD_RULES = ["EVD-05"]
_APPENDIX_RULES = ["SPW-09"]
_MERGE_RULES = ["SPW-35"]
_POSITIONING_RULE = "BRD-16"


def _positioning_required(active: dict) -> bool:
    return any(_POSITIONING_RULE in active["variants"][v]["stage_index"].get("slide_spec", [])
               for v in variants(active["profile"]))


def _require(run, name: str, hint: str) -> dict:
    data = load_json(run / name)
    if not data:
        raise SystemExit(hint)
    return data


def _load_inputs(deck_id: str):
    run = run_dir(deck_id)
    narrative = _require(run, "narrative_spec.json",
                          f"No narrative for {deck_id}. Run: python3 deck.py narrative develop {deck_id}")
    status = narrative.get("approval", {}).get("status")
    if status != "approved":
        raise SystemExit(
            f"Narrative for {deck_id} is not approved (status: {status}). "
            f"Run: python3 deck.py narrative qa {deck_id} && python3 deck.py narrative approve {deck_id}")
    ingested = _require(run, "ingested_spec.json",
                         f"No ingested spec for {deck_id}. Run: python3 deck.py ingest <profile> <source_dir>")
    active = _require(run, "active_rules.json",
                       f"No activation for {deck_id}. Run: python3 deck.py activate <profile>")
    return run, narrative, ingested, active


def _evidence_ref(eid: str, role: str, placement: str, rules: list[str]) -> dict:
    return {"ref": eid, "role": role, "placement": placement, "decided_by": rules}


_RISKS_PAGE_WORD_BUDGET = 55  # conservative margin under c_live_word_cap's 75-word live ceiling


def _word_count(text: str) -> int:
    return len((text or "").split())


def _paginate_risks(rows: list[dict]) -> list[list[dict]]:
    """Greedily pack risk rows onto pages so no single alternatives_risks slide's rows
    alone would blow the live word cap -- a 14-row deck forced onto one slide hit 791
    words against a 75-word ceiling in the first blind test; this scales to any row
    count instead of assuming a small, fixed number of risk items."""
    pages, page, page_words = [], [], 0
    for row in rows:
        row_words = _word_count(row["text"]) + _word_count(row["mitigation"])
        if page and page_words + row_words > _RISKS_PAGE_WORD_BUDGET:
            pages.append(page)
            page, page_words = [], 0
        page.append(row)
        page_words += row_words
    if page:
        pages.append(page)
    return _balance(pages, rows)


def _balance(pages: list[list[dict]], rows: list[dict]) -> list[list[dict]]:
    """Spread rows evenly over the same number of pages, so the last page is not a
    single orphaned row, as long as every page stays within the word budget."""
    n = len(pages)
    if n < 2:
        return pages
    per = -(-len(rows) // n)
    out = [rows[i:i + per] for i in range(0, len(rows), per)]
    fits = all(sum(_word_count(r["text"]) + _word_count(r["mitigation"]) for r in pg) <= _RISKS_PAGE_WORD_BUDGET
               or len(pg) == 1 for pg in out)
    return out if len(out) == n and fits else pages


def _spec_sections(frame: dict | None) -> list[dict]:
    """Spec sections from the approved frame. The section's question fills the divider's
    question line (manifest path section.eyebrow)."""
    if not frame:
        return []
    return [{"id": sec["id"], "title": sec["title"], "eyebrow": sec.get("question", ""),
             "summary": sec.get("summary", "")} for sec in frame.get("sections", [])]


def _order_by_sections(slide_plans: list[dict], frame: dict | None) -> list[dict]:
    """Order claim slides by the approved frame: sections in order, arguments in their
    section's order. Each plan gains "section". Arguments in no section keep their
    narrative order after the sectioned ones."""
    if not frame or not frame.get("sections"):
        return [dict(p, section=None) for p in slide_plans]
    rank = {}
    for si, sec in enumerate(frame["sections"]):
        for ai, aid in enumerate(sec.get("arguments", [])):
            rank.setdefault(aid, (si, ai, sec["id"]))
    tail = len(frame["sections"])
    ordered = sorted(enumerate(slide_plans),
                     key=lambda ip: (rank.get(ip[1]["arg_id"], (tail, ip[0], None))[:2]))
    return [dict(p, section=rank.get(p["arg_id"], (0, 0, None))[2]) for _, p in ordered]


OVERRIDES_FILE = "editorial_overrides.json"


def _resolved(slide: dict, evidence_by_id: dict, sources_by_id: dict) -> dict:
    """The renderer's view of one slide (evidence inlined, as spec.to_v1_view does), for
    checking fit before the spec is written."""
    from ..spec import _flat_source_string
    out = dict(slide)
    evs = []
    for r in slide.get("evidence", []):
        e = dict(evidence_by_id.get(r["ref"], {}))
        e.update(role=r.get("role"), placement=r.get("placement"))
        e["source"] = _flat_source_string(e.pop("provenance", {}) or {}, sources_by_id)
        evs.append(e)
    out["evidence"] = evs
    return out


def _fits_somewhere(engine, type_, view, ctx) -> tuple[bool, str]:
    """True if any variant of the layout type holds the slide, for live and for read."""
    read_type = engine.man["variant_policy"]["read_map"].get(type_, type_)
    why = []
    for variant, t in (("live", type_), ("read", read_type)):
        ok_any = False
        for lid in engine.by_type.get(t, []):
            ok, reason = engine.fits(lid, view, ctx, variant)
            if ok:
                ok_any = True
                break
            why.append(f"{lid}: {reason}")
        if not ok_any:
            return False, f"{variant}: " + "; ".join(why)
    return True, ""


def _split_to_fit(engine, slide, evidence_by_id, sources_by_id, ctx):
    """Keep a slide within some layout's capacity by moving trailing live points (and
    the second half of the read prose, if that is what overflows) onto a continuation
    slide, rather than accepting overflow. Returns (slides, note) where note explains
    any split or names the content that still does not fit."""
    from ..render.layout_engine import split_halves
    ok, why = _fits_somewhere(engine, slide["layout"], _resolved(slide, evidence_by_id, sources_by_id), ctx)
    if ok:
        return [slide], None
    live = list(slide["body"]["live"])
    read = slide["body"]["read"]
    first = dict(slide, body={"live": live, "read": read})
    moved_live, moved_read, read_split = [], "", False
    while True:
        ok, why2 = _fits_somewhere(engine, first["layout"], _resolved(first, evidence_by_id, sources_by_id), ctx)
        if ok:
            break
        if first["body"]["live"]:
            moved_live.insert(0, first["body"]["live"].pop())
            continue
        if not read_split and first["body"]["read"]:
            read_split = True
            keep, moved_read = split_halves(first["body"]["read"])
            if moved_read:
                first["body"]["read"] = keep
                continue
        return [slide], f"does not fit any variant even with its body moved: {why2}"
    cont = {"id": None, "layout": "claim_text", "section": slide.get("section"), "claim": slide.get("claim"),
            "headline": f"{slide['headline']} (continued)",
            "body": {"live": moved_live, "read": moved_read or " ".join(moved_live)},
            "evidence": [], "notes": ""}
    return [first, cont], f"split: {len(moved_live)} point(s){' and half the read prose' if moved_read else ''} " \
                          f"moved to a continuation slide because {why}"


def compose(deck_id: str) -> dict:
    from ..render.layout_engine import LayoutEngine
    from .content import compose_deck_content
    from .planner import CAVEAT_RULES, RISK_RULES, plan_deck

    run, narrative, ingested, active = _load_inputs(deck_id)
    decisions.clear_stage(run, "compose_editorial")
    evidence_by_id = {e["id"]: e for e in ingested["evidence"]}
    sources = ingested["sources"]
    sources_by_id = {x["id"]: x for x in sources}
    rules_text = compose_rules(active, ingested["evidence"])
    engine = LayoutEngine(ROOT / "template" / "layout_manifest.json")
    overrides = load_json(run / OVERRIDES_FILE, {}) or {}

    need_positioning = _positioning_required(active)
    positioning_text = None
    if need_positioning:
        brand = load_brand(ROOT / active["profile"]["brand"]) if active["profile"].get("brand") else {"verbatim": {}}
        positioning_text = brand.get("verbatim", {}).get("positioning") or None
        need_positioning = bool(positioning_text)

    frame = narrative.get("deck_frame")
    plan = plan_deck(narrative, evidence_by_id, engine, sources, overrides)
    content = compose_deck_content(narrative, plan, evidence_by_id, sources, rules_text,
                                   positioning_text=positioning_text)

    disposition: dict[str, str] = {}
    arg_to_slide: dict[str, str] = {}
    slides, claims, gaps = [], [], []
    cl_counter = 0

    if frame is None:
        decisions.log(run, stage="compose_editorial", source="MODEL", action="frame_generated_in_compose",
                      target={"type": "deck", "id": deck_id},
                      result="The approved narrative has no deck frame, so compose wrote the title and "
                             "closing itself. Revise the narrative to have them approved with the argument.",
                      review_required=True)
    title = frame["title"] if frame else content["title"]
    slides.append({"id": None, "layout": "title", "section": None,
                   "headline": title.get("headline", ""), "subhead": title.get("subhead", ""),
                   "body": {"live": [], "read": ""}, "evidence": [], "logo": True, "notes": ""})
    sections = _spec_sections(frame)
    use_dividers = len(sections) >= 2
    seen_sections: set[str] = set()

    is_persuasion = persuasion.method(narrative) == "persuasion"
    exec_summary = content["exec_summary"]
    points = (exec_summary.get("points") or [])[:3]
    exec_read = " ".join(p.rstrip(".") + "." for p in points)
    if is_persuasion:
        # PER-29: the ask, then its three strongest reasons, then the main thing still open (PER-15).
        exec_read = " ".join(x for x in (exec_summary.get("headline", "").rstrip(".") + ".", exec_read,
                                         exec_summary.get("open_note", "")) if x.strip(". "))
    slides.append({"id": None, "layout": "exec_summary", "section": None, "variant": "A",
                   "headline": exec_summary.get("headline", ""),
                   "body": {"live": points, "read": exec_read},
                   "evidence": [], "notes": exec_summary.get("notes", "") if is_persuasion else ""})
    if need_positioning:
        slides.append({"id": None, "layout": "positioning", "section": None,
                       "headline": content["positioning"].get("headline", ""),
                       "body": {"live": [positioning_text], "read": positioning_text}, "evidence": [], "notes": ""})

    coverage = engine.man.get("pattern_coverage", {})
    flow = {h["argument"]: h for h in narrative.get("headline_flow") or []}
    for p in plan["slides"]:
        sec_id = p.get("section") if sections else None
        if use_dividers and sec_id and sec_id not in seen_sections:
            seen_sections.add(sec_id)
            slides.append({"id": None, "layout": "section_divider", "section": sec_id,
                           "headline": next(x["title"] for x in sections if x["id"] == sec_id),
                           "body": {"live": [], "read": ""}, "evidence": [], "notes": ""})
        cl_counter += 1
        cl_id = f"CL{cl_counter:02d}"
        arg = next(a for a in narrative["arguments"] if a["id"] == p["arg_id"])
        c = content["slides"].get(p["arg_id"], {})
        refs = []
        if p["primary_evidence"]:
            refs.append(_evidence_ref(p["primary_evidence"], "primary", "slide", p["rules"]))
            disposition[p["primary_evidence"]] = "primary"
        if p["quote_evidence"]:
            refs.append(_evidence_ref(p["quote_evidence"], "supporting", "slide", p["rules"]))
            disposition.setdefault(p["quote_evidence"], "supporting")
        for eid in p["other_evidence"]:
            refs.append(_evidence_ref(eid, "supporting", "notes", _SUPPORTING_RULES))
            disposition.setdefault(eid, "supporting")
        fixed = flow.get(p["arg_id"])  # PER-46: the approved headline is used word for word
        slide = {"id": None, "layout": p["layout"], "section": sec_id, "claim": cl_id,
                 "headline": fixed["headline"] if fixed else c.get("headline", ""),
                 "body": c.get("body", {"live": [], "read": ""}),
                 "evidence": refs, "notes": c.get("notes", "")}
        if fixed and (c.get("subhead") or "").strip():
            slide["subhead"] = c["subhead"].strip()
        claims.append({"id": cl_id, "statement": arg["statement"], "evidence": arg.get("evidence", []),
                       "argument_ref": arg["id"]})
        ctx = {"section": next((x for x in sections if x["id"] == sec_id), None),
               "section_index": 1, "deck": {"logo": "Helix Bioworks"}}
        pieces, note = _split_to_fit(engine, slide, evidence_by_id, sources_by_id, ctx)
        p["_pieces"], p["_split_note"] = pieces, note
        slides += pieces
        p["_coverage_gap"] = coverage.get(p["pattern"], {}).get("gap")

    claim_end = len(slides)
    objections_at = None
    risk_pages = []
    if plan["risks"]["live"]:
        rows_by_id = {r["id"]: r for r in content["risks"]}
        rows = []
        for it in plan["risks"]["live"]:
            r = rows_by_id.get(it["id"], {})
            full = it["source"].get("statement") or it["source"].get("note", "")
            if it["kind"] == "objection" and it["source"].get("held_by"):
                full = f"{it['source']['held_by']}: {full}"
            rows.append({"id": it["id"], "text": r.get("text") or full, "mitigation": r.get("mitigation", ""),
                         "full": full})
            for eid in it["evidence"]:
                disposition.setdefault(eid, "supporting")
        risks_headline = content.get("risks_headline", "")
        for page_num, page_rows in enumerate(_paginate_risks(rows), start=1):
            headline = risks_headline if page_num == 1 else f"{risks_headline} (continued)"
            read_body = "\n\n".join(f"{r['id']}: {r['full']} Response: {r['mitigation']}".strip() for r in page_rows)
            page = {"id": None, "layout": "alternatives_risks", "section": None, "headline": headline,
                    "body": {"live": [], "read": read_body}, "evidence": [],
                    "risks": [{k: r[k] for k in ("id", "text", "mitigation")} for r in page_rows],
                    "notes": content.get("objections_notes", "") if is_persuasion and page_num == 1 else ""}
            risk_pages.append(page)
            slides.append(page)
        objections_at = _place_objections(slides, claim_end, risk_pages, frame) if is_persuasion else None

    closing = frame["closing"] if frame else content["closing"]
    slides.append({"id": None, "layout": "closing", "section": None, "headline": closing.get("headline", ""),
                   "body": {"live": [closing.get("ask", "")], "read": closing.get("ask", "")},
                   "evidence": [], "logo": True, "notes": ""})

    for i, sl in enumerate(slides, start=1):
        sl["id"] = f"SL{i:02d}"
    for p in plan["slides"]:
        arg_to_slide[p["arg_id"]] = p["_pieces"][0]["id"]

    # ---- decisions for every planning choice ----
    for p in plan["slides"]:
        sid = arg_to_slide[p["arg_id"]]
        detail = f"pattern {p['pattern']} ({p['pattern_how']}), {p['fit']} fit"
        if p["rejected"]:
            detail += "; rejected: " + "; ".join(p["rejected"])
        decisions.log(run, stage="compose_editorial", source="KB", action="select_layout",
                      target={"type": "slide", "id": sid}, result=p["layout"], rationale=detail,
                      kb_rules=p["rules"], review_required=p["fit"] != "strong")
        if p["_coverage_gap"] or p["fit"] != "strong":
            gaps.append({"slide": sid, "arg": p["arg_id"], "pattern": p["pattern"], "used": p["layout"],
                         "need": p["_coverage_gap"] or f"No strong layout could draw this {p['pattern']} evidence.",
                         "rejected": p["rejected"]})
            decisions.log(run, stage="compose_editorial", source="TEMPLATE", action="layout_gap",
                          target={"type": "slide", "id": sid},
                          result=f"{p['pattern']} content placed on {p['layout']}, not a strong layout for it",
                          rationale=p["_coverage_gap"] or "; ".join(p["rejected"]), review_required=True)
        if p["unshown_patterns"]:
            gaps.append({"slide": sid, "arg": p["arg_id"], "pattern": ", ".join(p["unshown_patterns"]),
                         "used": p["layout"], "unshown": True,
                         "need": "Supporting evidence with this structure sits in the notes, not on the slide.",
                         "rejected": []})
        for cv in p["caveats"]:
            decisions.log(run, stage="compose_editorial", source="KB", action="state_caveat",
                          target={"type": "slide", "id": sid},
                          result=f"{cv} bears on evidence this slide shows, so it is stated on the slide",
                          kb_rules=CAVEAT_RULES)
        if p["_split_note"]:
            split_ok = len(p["_pieces"]) > 1
            decisions.log(run, stage="compose_editorial", source="KB",
                          action="split_slide" if split_ok else "no_fit",
                          target={"type": "slide", "id": sid}, result=p["_split_note"],
                          kb_rules=["DSN-53", "SPW-01", "NAR-23"], review_required=not split_ok)
            if not split_ok:
                gaps.append({"slide": sid, "arg": p["arg_id"], "pattern": p["pattern"], "used": p["layout"],
                             "need": p["_split_note"], "rejected": []})

    if is_persuasion:
        _log_persuasion(run, deck_id, slides, risk_pages, plan, objections_at)

    for it in plan["risks"]["live"]:
        decisions.log(run, stage="compose_editorial", source="KB", action="risk_live",
                      target={"type": it["kind"], "id": it["id"]},
                      result=f"On the live risks slides (rank {it['rank']}: {it['why']})", kb_rules=RISK_RULES)

    appendix = []
    for entry in plan["appendix"]:
        eid = entry["evidence_id"]
        ev = evidence_by_id.get(eid, {})
        aid = f"A{len(appendix) + 1}"
        appendix.append({"id": aid, "kind": "table" if ev.get("table") else "evidence",
                         "parent_claim": arg_to_slide.get(entry["parent_arg_id"]),
                         "title": (ev.get("table") or {}).get("title") or ev.get("text_live") or eid,
                         **({"table": ev["table"]} if ev.get("table") else {"text": ev.get("text_read", "")}),
                         "evidence_ref": eid, "decision": entry["why"]})
        disposition.setdefault(eid, "supporting")
        decisions.log(run, stage="compose_editorial", source="KB", action="route_to_appendix",
                      target={"type": "evidence", "id": eid},
                      result=f"Routed to appendix entry {aid}: {entry['why']}", kb_rules=_APPENDIX_RULES + ["DAT-05"])
    first_risk_page = risk_pages[0]["id"] if risk_pages else None
    for it in plan["risks"]["appendix"]:
        aid = f"A{len(appendix) + 1}"
        text = it["source"].get("statement") or it["source"].get("note", "")
        parent = next((arg_to_slide[a] for a in it.get("shown_on", []) if a in arg_to_slide), first_risk_page)
        if it["kind"] == "objection":
            text = f"{it['source'].get('held_by', '')}: {text} Answer: {it['source'].get('answer', '')}".strip(": ")
        appendix.append({"id": aid, "kind": "risk", **({"parent_claim": parent} if parent else {}),
                         "title": f"{'Objection' if it['kind'] == 'objection' else 'Open question'} {it['id']}",
                         "text": text,
                         "evidence_refs": sorted(it["evidence"]), "source_ref": it["id"], "decision": it["why"]})
        for eid in it["evidence"]:
            disposition.setdefault(eid, "supporting")
        decisions.log(run, stage="compose_editorial", source="KB", action="risk_to_appendix",
                      target={"type": it["kind"], "id": it["id"]},
                      result=f"Moved to appendix entry {aid}: ranked below the {len(plan['risks']['live'])} live "
                             f"risk rows ({it['why']}). Promote with: python3 deck.py appendix promote {deck_id} {aid}",
                      kb_rules=RISK_RULES + ["SPW-11"], review_required=True)

    new_evidence = []
    for e in ingested["evidence"]:
        e = dict(e)
        e["disposition"] = disposition.get(e["id"], "background")
        new_evidence.append(e)
        if e["disposition"] == "background":
            decisions.log(run, stage="compose_editorial", source="KB", action="classify_background",
                          target={"type": "evidence", "id": e["id"]},
                          result=f"{e['id']} is not cited by any slide, argument, or risks row; "
                                 f"left as background rather than forced into the deck.")

    deck_spec = {
        "spec_version": "0.2", "deck_id": deck_id, "status": "composed",
        "profile": ingested["profile"], "deck": {"logo": "Helix Bioworks"},
        "thesis": narrative["thesis"], "sections": sections,
        "sources": sources, "evidence": new_evidence,
        "claims": claims, "slides": slides, "appendix": appendix,
        "flags": ingested.get("flags", []),
    }
    errors = validate(deck_spec)
    if errors:
        raise SystemExit("Composed deck_spec failed validation:\n" + "\n".join(f"  {e}" for e in errors))

    save_json(run / "deck_spec.json", deck_spec)
    _write_report(run, deck_spec, plan)
    _write_gaps(run, deck_id, gaps)
    return deck_spec


def _place_objections(slides: list[dict], claim_end: int, risk_pages: list[dict], frame: dict | None) -> str | None:
    """PER-13: move the objections pages from their KB position (after the last claim,
    before the closing) to just after the section the approved frame names. Returns that
    section id, or None if the frame names none and the KB position stands."""
    after = (frame or {}).get("objections_after") or ""
    if not risk_pages or not after:
        return None
    idx = [i for i, sl in enumerate(slides[:claim_end]) if sl.get("section") == after]
    if not idx:
        return None
    del slides[claim_end:claim_end + len(risk_pages)]
    slides[idx[-1] + 1:idx[-1] + 1] = risk_pages
    return after


def _log_persuasion(run, deck_id: str, slides: list[dict], risk_pages: list[dict], plan: dict,
                    objections_at: str | None):
    """Persuasion-layer choices that change the deck compared with the KB alone, logged
    with the KB default the same way brand overrides are (kb/persuasion_v1.json,
    logged_outcome_changes)."""
    ex = next(sl for sl in slides if sl["layout"] == "exec_summary")
    decisions.log(run, stage="compose_editorial", source="PERSUASION", action="exec_summary_as_ask",
                  target={"type": "slide", "id": ex["id"]},
                  result=f"Executive summary states the ask with three reasons: {ex['headline']}",
                  kb_rules=["PER-29", "NAR-38"],
                  kb_default="The executive summary headline states the thesis, with three supporting points.")
    objections = [it for it in plan["risks"]["live"] if it["kind"] == "objection"]
    if objections:
        decisions.log(run, stage="compose_editorial", source="PERSUASION", action="objections_register",
                      target={"type": "deck", "id": deck_id},
                      result=f"Live rows are the brief's ranked objections: {', '.join(it['id'] for it in objections)}",
                      kb_rules=["PER-13", "NAR-19", "NAR-59"],
                      kb_default="Live risk rows are unresolved questions and counterarguments ranked by flag and "
                                 "slide links.")
    if risk_pages:
        pos = slides.index(risk_pages[0]) + 1
        where = (f"after section {objections_at}, slide {pos} of {len(slides)}" if objections_at
                 else f"the frame names no section, so the KB position stands: slide {pos} of {len(slides)}")
        decisions.log(run, stage="compose_editorial", source="PERSUASION", action="place_objections",
                      target={"type": "slide", "id": risk_pages[0]["id"]}, result=f"Objections answered {where}",
                      kb_rules=["PER-13", "NAR-19"],
                      kb_default="The risks slides sit after the last claim section, just before the closing.")


def _write_gaps(run, deck_id: str, gaps: list[dict]):
    """layout_gaps.md: content the layout library could not show well, with what a
    layout would need. Written every run so an empty file means no gaps."""
    lines = [f"# Layout gaps: {deck_id}", ""]
    hard = [g for g in gaps if not g.get("unshown")]
    soft = [g for g in gaps if g.get("unshown")]
    if not gaps:
        lines.append("No gaps: every slide's content was placed on a strong layout for its pattern.")
    if hard:
        lines += ["## Needs a layout (review required)", ""]
        for g in hard:
            lines.append(f"- **{g['slide']}** ({g['arg']}, {g['pattern']}): placed on `{g['used']}`. Needed: {g['need']}")
            if g["rejected"]:
                lines.append(f"  Rejected: {'; '.join(g['rejected'])}")
    if soft:
        lines += ["", "## Evidence not shown on its slide", "",
                  "These slides carry supporting evidence with a different structure in their notes. A richer layout "
                  "could show it beside the main evidence.", ""]
        lines += [f"- **{g['slide']}** ({g['arg']}): {g['pattern']} evidence, slide uses `{g['used']}`." for g in soft]
    (run / "layout_gaps.md").write_text("\n".join(lines) + "\n")


def _write_report(run, deck_spec: dict, plan: dict):
    lines = [f"# Compose report: {deck_spec['deck_id']}", "",
             f"{len(deck_spec['slides'])} slide(s), {len(deck_spec['claims'])} claim(s), "
             f"{len(deck_spec['appendix'])} appendix item(s).", ""]
    if not deck_spec["sections"]:
        lines += ["No sections: the approved narrative's deck frame has none, or the narrative "
                  "predates deck frames.", ""]
    lines += ["| Slide | Layout | Claim | Headline |", "|---|---|---|---|"]
    lines += [f"| {s['id']} | {s['layout']} | {s.get('claim') or '-'} | {s.get('headline', '')[:80]} |"
              for s in deck_spec["slides"]]
    if deck_spec["appendix"]:
        lines += ["", "## Appendix", ""]
        lines += [f"- **{a['id']}** ({a.get('kind', 'table')}): {a['title'][:90]}. {a.get('decision', '')}"
                  for a in deck_spec["appendix"]]
    (run / "compose_report.md").write_text("\n".join(lines) + "\n")
