"""
Build a first-draft deck from Claude's draft.json, with no model call in the engine.

The plugin splits the work the CLI pipeline gives to `claude -p`:
- The host Claude (the conversation the user is already in) makes the judgment calls:
  what the audience must decide, the story, which facts carry it, what to cut, what
  conflicts, and the words on each slide. It writes them to draft.json with a verbatim
  quote and locator for every piece of evidence, and a judgment log of its choices.
- This module does everything that should not depend on a model's say-so: it checks
  every quote and figure against the sources (verify.py), picks each slide's layout from
  the structure of its evidence (compose.planner, the same planner the CLI uses), splits
  slides that overflow rather than cramming them, paginates the risk register, routes
  oversized evidence to the appendix, assembles and schema-validates a v0.2 spec, runs
  the deterministic linter, renders the PowerPoint and HTML versions from the layout
  template, and writes every choice to the decision log.

Output lands in <project>/out/, with a human-facing REVIEW.md at the project root.
"""
from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

from .. import decisions
from ..common import ROOT, load_json, save_json
from ..patterns import pattern_ids
from ..spec import to_v1_view, validate
from . import verify

STAGE = "draft"
BRAND_PATH = "brand/helix_bioworks.brand.json"
KINDS = {"text", "big_number", "chart", "comparison", "diagram", "table"}
VERIFICATIONS = {"source-supported", "derived", "interpreted", "unverified"}
DELIVERY = {"live", "read", "both"}
REVIEW_BY_DEFAULT = {"cut", "conflict", "assumption", "placeholder", "gap", "tone", "reframe", "exclude_source"}


class DraftError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


# ---------------------------------------------------------------- draft checks

def _visible_text(draft: dict) -> list[tuple[str, str]]:
    """(where, text) for every string a reader or listener will see."""
    out = [("title.headline", draft.get("title", {}).get("headline", "")),
           ("title.subhead", draft.get("title", {}).get("subhead", ""))]
    ex = draft.get("exec_summary", {})
    out += [("exec_summary.headline", ex.get("headline", "")), ("exec_summary.open_note", ex.get("open_note", "")),
            ("exec_summary.notes", ex.get("notes", ""))]
    out += [(f"exec_summary.points[{i}]", p) for i, p in enumerate(ex.get("points", []))]
    for i, s in enumerate(draft.get("slides", [])):
        for k in ("headline", "subhead", "read", "notes"):
            out.append((f"slides[{i}].{k}", s.get(k, "")))
        out += [(f"slides[{i}].points[{j}]", p) for j, p in enumerate(s.get("points", []))]
    for i, r in enumerate((draft.get("risks") or {}).get("items", [])):
        out += [(f"risks.items[{i}].text", r.get("text", "")), (f"risks.items[{i}].response", r.get("response", ""))]
    out += [("risks.headline", (draft.get("risks") or {}).get("headline", "")),
            ("closing.headline", draft.get("closing", {}).get("headline", "")),
            ("closing.ask", draft.get("closing", {}).get("ask", ""))]
    out += [(f"sections[{i}].{k}", s.get(k, "")) for i, s in enumerate(draft.get("sections", []))
            for k in ("title", "question", "summary")]
    return [(w, t) for w, t in out if isinstance(t, str) and t]


def check_draft(draft: dict, intake: dict) -> list[str]:
    """Structural problems that stop a build. Each names the field and the fix."""
    errs = []
    readable = {s["id"] for s in intake["sources"] if s["status"] == "read"}
    for key in ("brief", "story", "evidence", "title", "exec_summary", "slides", "closing"):
        if key not in draft:
            errs.append(f"missing top-level '{key}'")
    if errs:
        return errs
    b = draft["brief"]
    for k in ("audience", "purpose", "ask"):
        if not (b.get(k) or "").strip():
            errs.append(f"brief.{k} is empty")
    if b.get("delivery", "live") not in DELIVERY:
        errs.append(f"brief.delivery must be one of {sorted(DELIVERY)}")
    if not (draft["story"].get("thesis") or "").strip():
        errs.append("story.thesis is empty")

    ev_ids = set()
    for i, e in enumerate(draft["evidence"]):
        where = f"evidence[{i}] ({e.get('id', '?')})"
        if not re.fullmatch(r"EV\d+", e.get("id", "")):
            errs.append(f"{where}: id must look like EV01")
        elif e["id"] in ev_ids:
            errs.append(f"{where}: duplicate id")
        ev_ids.add(e.get("id"))
        if e.get("source") not in readable:
            errs.append(f"{where}: source {e.get('source')!r} is not a readable source in intake.json")
        if e.get("kind", "text") not in KINDS:
            errs.append(f"{where}: kind must be one of {sorted(KINDS)}")
        captured = e.get("kind") in ("table", "chart") and _captured(intake, e) is not None
        if not captured and not (e.get("quote") or "").strip():
            errs.append(f"{where}: quote is empty. Copy the exact words from the source.")
        if not (e.get("locator") or "").strip():
            errs.append(f"{where}: locator is empty. Use the marker at the start of the source line, e.g. L12 or P4.")
        if e.get("verification", "source-supported") not in VERIFICATIONS:
            errs.append(f"{where}: verification must be one of {sorted(VERIFICATIONS)}")
        if e.get("pattern") and e["pattern"] not in pattern_ids():
            errs.append(f"{where}: pattern {e['pattern']!r} unknown; use one of {pattern_ids()}")

    sec_ids = {s.get("id") for s in draft.get("sections", [])}
    if not draft["slides"]:
        errs.append("slides is empty")
    for i, s in enumerate(draft["slides"]):
        if not (s.get("headline") or "").strip():
            errs.append(f"slides[{i}]: headline is empty")
        for eid in s.get("evidence", []):
            if eid not in ev_ids:
                errs.append(f"slides[{i}]: evidence {eid} is not in the evidence list")
        if s.get("show") and s["show"] not in s.get("evidence", []):
            errs.append(f"slides[{i}]: show {s['show']} must also be listed in the slide's evidence")
        if s.get("section") and s["section"] not in sec_ids:
            errs.append(f"slides[{i}]: section {s['section']} is not in sections")
    for i, r in enumerate((draft.get("risks") or {}).get("items", [])):
        for eid in r.get("evidence", []):
            if eid not in ev_ids:
                errs.append(f"risks.items[{i}]: evidence {eid} is not in the evidence list")
    for i, a in enumerate(draft.get("appendix", [])):
        for eid in a.get("evidence", []):
            if eid not in ev_ids:
                errs.append(f"appendix[{i}]: evidence {eid} is not in the evidence list")
    for i, c in enumerate(draft.get("conflicts", [])):
        if len([x for x in c.get("evidence", []) if x in ev_ids]) < 2:
            errs.append(f"conflicts[{i}]: name at least two evidence ids that disagree")
    for where, text in _visible_text(draft):
        ids = verify.visible_ids(text)
        if ids:
            errs.append(f"{where}: internal id(s) {', '.join(ids)} in visible text. Name the source or point in words.")
    if not (draft["closing"].get("ask") or "").strip():
        errs.append("closing.ask is empty")
    return errs


def _captured(intake: dict, e: dict):
    """The table or chart the reader copied from the file at this locator, if any. A
    table carries its source and, when the draft states them, its units (DAT-04)."""
    cap = intake.get("captured", {}).get(e.get("source"), {})
    loc = (e.get("locator") or "").strip()
    fname = next((s.get("filename") for s in intake["sources"] if s["id"] == e.get("source")), e.get("source"))
    for t in cap.get("tables", []):
        if t["locator"] == loc:
            table = {"title": e.get("text_live") or f"{loc}", "columns": t["columns"], "rows": t["rows"],
                     "source": f"{fname}, {loc}"}
            if e.get("unit"):
                table["units"] = e["unit"]
            return "table", table
    for c in cap.get("charts", []):
        if c["locator"] == loc:
            return "chart", c["chart"]
    return None


# ---------------------------------------------------------------- evidence

def _evidence_records(draft, intake, texts, run) -> tuple[list[dict], dict]:
    """v0.2 evidence records, verified against their sources. Returns (records, issues)
    where issues lists what a human must look at."""
    issues = {"unverified": [], "locator_fixed": [], "numbers": [], "payload": []}
    out = []
    for e in draft["evidence"]:
        text = texts[e["source"]]
        kind = e.get("kind", "text")
        rec = {"id": e["id"], "kind": kind, "claim_type": e.get("claim_type"), "disposition": "unverified",
               "text_live": e.get("text_live") or e.get("quote", "")[:80],
               "text_read": e.get("text_read") or e.get("quote", "")}
        verification = e.get("verification", "source-supported")
        locator = e["locator"].strip()

        cap = _captured(intake, e) if kind in ("table", "chart") else None
        if cap:
            rec["kind"], rec[cap[0]] = cap[0], cap[1]
            decisions.log(run, stage=STAGE, source="TEMPLATE", action="payload_from_file",
                          target={"type": "evidence", "id": e["id"]},
                          result=f"{cap[0]} copied cell for cell from {e['source']} {locator}, not from the draft")
        else:
            if e.get("quote"):
                found = verify.find_quote(e["quote"], text)
                if not found:
                    verification = "unverified"
                    issues["unverified"].append(e["id"])
                    decisions.log(run, stage=STAGE, source="MODEL", action="quote_not_found",
                                  target={"type": "evidence", "id": e["id"]},
                                  result=f"Quote not found in {e['source']}: \"{e['quote'][:120]}\". Kept, marked "
                                         f"unverified. Check it against the source or remove it.",
                                  review_required=True)
                elif not verify.locator_matches(locator, found):
                    new = verify.locator_from(found)
                    issues["locator_fixed"].append(e["id"])
                    decisions.log(run, stage=STAGE, source="TEMPLATE", action="locator_corrected",
                                  target={"type": "evidence", "id": e["id"]},
                                  result=f"Locator {locator} corrected to {new}, where the quote was found")
                    locator = new
            for k in ("chart", "comparison", "diagram", "table"):
                if kind == k and isinstance(e.get(k), dict):
                    rec[k] = e[k]
            if kind in ("chart", "comparison", "diagram", "table"):
                from ..ingest.payloads import check_payload
                chk = check_payload(rec, text)
                if chk["shape"] or chk["unsupported_numbers"] or not rec.get(kind):
                    why = ("; ".join(chk["shape"]) or
                           (f"number(s) not in the source: {', '.join(chk['unsupported_numbers'])}"
                            if chk["unsupported_numbers"] else f"no {kind} payload given"))
                    issues["payload"].append(e["id"])
                    decisions.log(run, stage=STAGE, source="MODEL", action="payload_rejected",
                                  target={"type": "evidence", "id": e["id"]},
                                  result=f"{kind} payload removed ({why}); kept as text so the fact is not lost",
                                  review_required=bool(chk["unsupported_numbers"]))
                    rec.pop(kind, None)
                    rec["kind"] = "text"

        # A payload copied from the file needs no check; the draft's own wording and any
        # payload the draft wrote do.
        checked = dict(e) if cap else dict(e, **{k: rec.get(k) for k in ("chart", "table")})
        if cap:
            checked.pop(cap[0], None)
        missing = verify.unsupported_numbers(checked, text)
        if missing and verification not in ("derived",):
            issues["numbers"].append((e["id"], missing))
            decisions.log(run, stage=STAGE, source="MODEL", action="figure_not_in_source",
                          target={"type": "evidence", "id": e["id"]},
                          result=f"Figure(s) {', '.join(missing)} do not appear in {e['source']}. "
                                 f"Mark as derived with the arithmetic in transform, or correct them.",
                          review_required=True)
        if rec["kind"] == "big_number":
            rec["value"] = e.get("value") or rec["text_live"]
            rec["label"] = e.get("label") or ""
        if e.get("pattern") in pattern_ids():
            rec["information_pattern"] = e["pattern"]
        rec["provenance"] = {"source_id": e["source"], "locator": locator, "original_value": e.get("quote", ""),
                             "unit": e.get("unit", ""), "transform": e.get("transform", ""),
                             "verification": verification}
        out.append(rec)
    return out, issues


# ---------------------------------------------------------------- profile and activation

def _profile(draft: dict, deck_id: str) -> dict:
    b = draft["brief"]
    return {
        "deck_id": deck_id, "deck_type": b.get("deck_type", "other"), "brand": BRAND_PATH,
        "channels": b.get("channels") or ["internal"], "purpose": b["purpose"],
        "audience": {"primary": b["audience"], "segments": b.get("segments", []),
                     "expertise": b.get("expertise", "mixed"), "disposition": b.get("disposition", "evaluating"),
                     "trust_in_presenter": b.get("trust", "medium"),
                     "decision_authority": b.get("decision_maker", ""), "known_preferences": b.get("preferences", []),
                     "non_native_speakers": False},
        "delivery": {"mode": b.get("delivery", "live"), "pre_read": b.get("delivery") == "both",
                     "setting": b.get("setting", "small_meeting"), "time_minutes": b.get("time_minutes", 0),
                     "presenter": b.get("presenter")},
        "constraints": {"placeholder_policy": "Figures not in the sources appear as [bracketed] placeholders."},
        "notes": "Built by the deck-drafter plugin from draft.json (no-model engine mode).",
    }


# ---------------------------------------------------------------- assembly

def _slide_numbers_check(run, slides_out, by_id, all_numbers, claims) -> list[tuple[str, list[str]]]:
    """A claim slide's figures may come from any evidence the draft cited for it,
    including evidence the planner sent to the appendix because it was too big to show."""
    flagged = []
    claim_ev = {c["id"]: c["evidence"] for c in claims}
    for sl in slides_out:
        texts = [sl.get("headline", ""), sl.get("subhead", ""), sl["body"].get("read", ""), sl.get("notes", "")]
        texts += sl["body"].get("live", [])
        for r in sl.get("risks", []):
            texts += [r.get("text", ""), r.get("mitigation", "")]
        ids = {r["ref"] for r in sl.get("evidence", [])} | set(claim_ev.get(sl.get("claim"), []))
        cited = [by_id[x] for x in sorted(ids) if x in by_id]
        allowed = set().union(*(verify.evidence_numbers(_with_quote(e)) for e in cited)) if cited else set()
        if sl["layout"] in ("title", "exec_summary", "closing", "section_divider", "alternatives_risks", "positioning") \
                or not cited:
            allowed = all_numbers
        nums = set().union(*(verify.numbers_in(t) for t in texts))
        bad = sorted(n for n in nums if n not in allowed)
        if bad:
            flagged.append((sl["id"], bad))
            decisions.log(run, stage=STAGE, source="MODEL", action="slide_figure_unsupported",
                          target={"type": "slide", "id": sl["id"]},
                          result=f"Figure(s) {', '.join(bad)} on the slide are not in the evidence it cites. "
                                 f"Cite the evidence, bracket it as a placeholder, or remove it.",
                          review_required=True)
    return flagged


def _chart_table(e: dict, sources_by_id: dict) -> dict | None:
    c = e.get("chart") or {}
    prov = e.get("provenance", {})
    fname = sources_by_id.get(prov.get("source_id"), {}).get("filename", "")
    source = ", ".join(x for x in (fname, prov.get("locator", "")) if x)
    if c.get("categories") and c.get("series"):
        return {"title": c.get("title") or e.get("text_live", ""), "units": c.get("unit") or "as in source",
                "source": source, "columns": ["Category"] + [se["name"] for se in c["series"]],
                "rows": [[cat] + [str(se["values"][i]) for se in c["series"]] for i, cat in enumerate(c["categories"])]}
    if c.get("type") == "scatter" and c.get("series"):
        return {"title": c.get("title") or e.get("text_live", ""), "units": "as in source", "source": source,
                "columns": ["Series", c.get("x_label", "x"), c.get("y_label", "y")],
                "rows": [[se["name"], str(x), str(y)] for se in c["series"] for x, y in se["points"]]}
    return None


def _with_quote(e: dict) -> dict:
    return dict(e, quote=(e.get("provenance") or {}).get("original_value", ""))


def build(project) -> dict:
    from ..compose.pipeline import _evidence_ref, _paginate_risks, _positioning_required, _split_to_fit, _write_gaps
    from ..compose.planner import plan_argument
    from ..render.layout_engine import LayoutEngine

    project = Path(project)
    intake = load_json(project / "intake.json")
    if not intake:
        raise DraftError(["No intake.json. Run intake first."])
    draft = load_json(project / "draft.json")
    if not draft:
        raise DraftError(["No draft.json in the project folder."])
    errs = check_draft(draft, intake)
    if errs:
        (project / "build_errors.md").write_text("# Draft problems to fix\n\n" + "\n".join(f"- {e}" for e in errs) + "\n")
        raise DraftError(errs)
    (project / "build_errors.md").unlink(missing_ok=True)

    deck_id = re.sub(r"[^a-z0-9-]+", "-", (draft.get("deck_id") or project.name).lower()).strip("-") or "draft"
    run = project / "out"
    run.mkdir(exist_ok=True)
    decisions.clear_stage(run, STAGE)
    texts = {s["id"]: (project / s["text_file"]).read_text() for s in intake["sources"] if s["status"] == "read"}
    sources = []
    for s in intake["sources"]:
        if s["status"] != "read":
            continue
        rec = {"id": s["id"], "filename": s["filename"], "doc_type": s["doc_type"], "note": s.get("note", "")}
        if s.get("google"):
            rec["google"] = s["google"]
        sources.append(rec)
    sources_by_id = {s["id"]: s for s in sources}

    # ---- judgment the host Claude made, logged first so the log reads in decision order
    for j in draft.get("judgment", []):
        jtype = j.get("type", "judgment")
        decisions.log(run, stage=STAGE, source="MODEL", action=jtype,
                      target={"type": "deck", "id": j.get("target") or deck_id},
                      result=j.get("decision", ""), rationale=j.get("why", ""), kb_rules=j.get("rules", []),
                      review_required=bool(j.get("review", jtype in REVIEW_BY_DEFAULT)))

    evidence, issues = _evidence_records(draft, intake, texts, run)
    by_id = {e["id"]: e for e in evidence}

    # ---- profile and activation (no classifier: conditional rules stay in force)
    from ..activate import activate
    profile = _profile(draft, deck_id)
    save_json(run / "profile.json", profile)
    with contextlib.redirect_stdout(io.StringIO()):
        active = activate(str(run / "profile.json"), unclassified=True, run=run)

    # ---- slides
    engine = LayoutEngine(ROOT / "template" / "layout_manifest.json")
    sections = [{"id": s["id"], "title": s.get("title", ""), "eyebrow": s.get("question", ""),
                 "summary": s.get("summary", "")} for s in draft.get("sections", [])]
    use_dividers = len(sections) >= 2
    ex = draft["exec_summary"]
    points = (ex.get("points") or [])[:3]
    if len(ex.get("points") or []) > 3:
        decisions.log(run, stage=STAGE, source="KB", action="trim_exec_points",
                      target={"type": "slide", "id": "exec_summary"},
                      result=f"Kept the first 3 of {len(ex['points'])} summary points; the layout holds three",
                      kb_rules=["NAR-38"], review_required=True)
    slides = [{"id": None, "layout": "title", "section": None, "headline": draft["title"].get("headline", ""),
               "subhead": draft["title"].get("subhead", ""), "body": {"live": [], "read": ""}, "evidence": [],
               "logo": True, "notes": draft["title"].get("notes", "")},
              {"id": None, "layout": "exec_summary", "section": None, "variant": "A", "headline": ex.get("headline", ""),
               "body": {"live": points, "read": ex.get("read") or " ".join(
                   x.strip() for x in [ex.get("headline", "").rstrip(".") + "."] + [p.rstrip(".") + "." for p in points]
                   + [ex.get("open_note", "")] if x.strip(". "))},
               "evidence": [], "notes": ex.get("notes", "")}]
    if _positioning_required(active):
        from ..kb import load_brand
        text = load_brand(ROOT / BRAND_PATH).get("verbatim", {}).get("positioning")
        if text:
            slides.append({"id": None, "layout": "positioning", "section": None,
                           "headline": draft.get("positioning_headline") or "What Helix Bioworks is",
                           "body": {"live": [text], "read": text}, "evidence": [], "notes": ""})
            decisions.log(run, stage=STAGE, source="BRAND", action="positioning_slide",
                          target={"type": "deck", "id": deck_id}, brand_rules=["BRD-16"],
                          result="Brand positioning statement added verbatim; required for this channel")

    plans, claims, gaps, disposition, seen_sections = [], [], [], {}, set()
    taken: set = set()
    disposition_rank = {"primary": 0, "supporting": 1}
    for n, s in enumerate(draft["slides"], start=1):
        arg_id = f"ARG{n:02d}"
        sec_id = s.get("section") if sections else None
        if use_dividers and sec_id and sec_id not in seen_sections:
            seen_sections.add(sec_id)
            sec = next(x for x in sections if x["id"] == sec_id)
            slides.append({"id": None, "layout": "section_divider", "section": sec_id, "headline": sec["title"],
                           "body": {"live": [], "read": ""}, "evidence": [], "notes": ""})
        ev_ids = [e for e in s.get("evidence", []) if e in by_id]
        plan, note = None, ""
        if s.get("show"):
            trial = plan_argument({"id": arg_id, "role": "primary", "evidence": [s["show"]]}, by_id, engine,
                                  sources_by_id, set(taken))
            if trial["layout"] != "claim_text" or by_id[s["show"]]["kind"] == "text":
                plan = trial
                plan["other_evidence"] = [e for e in ev_ids if e not in (plan["primary_evidence"], plan["quote_evidence"])]
                taken.update(x for x in (plan["primary_evidence"], plan["quote_evidence"]) if x)
            else:
                note = f"Draft asked to show {s['show']}, but no layout can draw it ({'; '.join(trial['rejected'])})."
        if plan is None:
            plan = plan_argument({"id": arg_id, "role": "primary", "evidence": ev_ids}, by_id, engine,
                                 sources_by_id, taken)
        cl_id = f"CL{n:02d}"
        refs = []
        if plan["primary_evidence"]:
            refs.append(_evidence_ref(plan["primary_evidence"], "primary", "slide", plan["rules"]))
        if plan["quote_evidence"]:
            refs.append(_evidence_ref(plan["quote_evidence"], "supporting", "slide", plan["rules"]))
        refs += [_evidence_ref(e, "supporting", "notes", ["EVD-05"]) for e in plan["other_evidence"]]
        for r in refs:
            cur = disposition.get(r["ref"])
            if cur is None or disposition_rank[r["role"]] < disposition_rank[cur]:
                disposition[r["ref"]] = r["role"]
        live = [p for p in s.get("points", []) if p.strip()]
        read = s.get("read") or " ".join(x for x in [s.get("subhead", "")] + live if x)
        slide = {"id": None, "layout": plan["layout"], "section": sec_id, "claim": cl_id,
                 "headline": s["headline"].strip(), "body": {"live": live, "read": read},
                 "evidence": refs, "notes": s.get("notes", "")}
        if (s.get("subhead") or "").strip():
            slide["subhead"] = s["subhead"].strip()
        claims.append({"id": cl_id, "statement": s.get("claim") or s.get("subhead") or s["headline"],
                       "evidence": ev_ids, "argument_ref": arg_id})
        ctx = {"section": next((x for x in sections if x["id"] == sec_id), None), "section_index": 1,
               "deck": {"logo": "Helix Bioworks"}}
        pieces, split_note = _split_to_fit(engine, slide, by_id, sources_by_id, ctx)
        plan.update(arg_id=arg_id, _pieces=pieces, _split_note=split_note, _show_note=note, _job=s.get("job", ""),
                    _coverage_gap=engine.man.get("pattern_coverage", {}).get(plan["pattern"], {}).get("gap"))
        plans.append(plan)
        slides += pieces

    # ---- risks and objections
    risk_pages = []
    rd = draft.get("risks") or {}
    if rd.get("items"):
        rows = [{"id": f"R{i:02d}", "text": r.get("text", ""), "mitigation": r.get("response", ""),
                 "full": r.get("text", "")} for i, r in enumerate(rd["items"], start=1)]
        for r in rd["items"]:
            for eid in r.get("evidence", []):
                disposition.setdefault(eid, "supporting")
        head = rd.get("headline") or "What could stop this, and our answer"
        for page_num, page_rows in enumerate(_paginate_risks(rows), start=1):
            risk_pages.append({"id": None, "layout": "alternatives_risks", "section": None,
                               "headline": head if page_num == 1 else f"{head} (continued)",
                               "body": {"live": [], "read": "\n\n".join(
                                   f"{r['full']} Response: {r['mitigation']}".strip() for r in page_rows)},
                               "evidence": [], "risks": [{k: r[k] for k in ("id", "text", "mitigation")}
                                                         for r in page_rows],
                               "notes": rd.get("notes", "") if page_num == 1 else ""})
        after = rd.get("after_section")
        idx = [i for i, sl in enumerate(slides) if after and sl.get("section") == after]
        at = idx[-1] + 1 if idx else len(slides)
        slides[at:at] = risk_pages
        decisions.log(run, stage=STAGE, source="KB", action="place_risks", target={"type": "deck", "id": deck_id},
                      result=f"{len(rows)} risk row(s) on {len(risk_pages)} page(s), "
                             f"{'after section ' + after if idx else 'after the last claim'}",
                      kb_rules=["NAR-19", "NAR-59"])

    closing = draft["closing"]
    slides.append({"id": None, "layout": "closing", "section": None, "headline": closing.get("headline", ""),
                   "body": {"live": [closing.get("ask", "")], "read": closing.get("ask", "")}, "evidence": [],
                   "logo": True, "notes": closing.get("notes", "")})
    for i, sl in enumerate(slides, start=1):
        sl["id"] = f"SL{i:02d}"
    arg_to_slide = {p["arg_id"]: p["_pieces"][0]["id"] for p in plans}

    # ---- planning decisions
    for p in plans:
        sid = arg_to_slide[p["arg_id"]]
        detail = f"pattern {p['pattern']} ({p['pattern_how']}), {p['fit']} fit"
        if p["rejected"]:
            detail += "; rejected: " + "; ".join(p["rejected"])
        decisions.log(run, stage=STAGE, source="KB", action="select_layout", target={"type": "slide", "id": sid},
                      result=p["layout"], rationale=detail, kb_rules=p["rules"], review_required=p["fit"] != "strong")
        if p["_show_note"]:
            decisions.log(run, stage=STAGE, source="TEMPLATE", action="anchor_not_drawable",
                          target={"type": "slide", "id": sid}, result=p["_show_note"], review_required=True)
        if p["_coverage_gap"] or p["fit"] != "strong":
            gaps.append({"slide": sid, "arg": p["arg_id"], "pattern": p["pattern"], "used": p["layout"],
                         "need": p["_coverage_gap"] or f"No strong layout could draw this {p['pattern']} evidence.",
                         "rejected": p["rejected"]})
        if p["unshown_patterns"]:
            gaps.append({"slide": sid, "arg": p["arg_id"], "pattern": ", ".join(p["unshown_patterns"]),
                         "used": p["layout"], "unshown": True, "need": "", "rejected": []})
        if p["_split_note"]:
            ok = len(p["_pieces"]) > 1
            decisions.log(run, stage=STAGE, source="KB", action="split_slide" if ok else "no_fit",
                          target={"type": "slide", "id": sid}, result=p["_split_note"],
                          kb_rules=["DSN-53", "SPW-01", "NAR-23"], review_required=not ok)
            if not ok:
                gaps.append({"slide": sid, "arg": p["arg_id"], "pattern": p["pattern"], "used": p["layout"],
                             "need": p["_split_note"], "rejected": []})

    # ---- appendix
    appendix = []
    for p in plans:
        for eid in p["appendix_evidence"]:
            e = by_id[eid]
            aid = f"A{len(appendix) + 1}"
            appendix.append({"id": aid, "kind": "table" if e.get("table") else "evidence",
                             "parent_claim": arg_to_slide[p["arg_id"]],
                             "title": (e.get("table") or {}).get("title") or e.get("text_live") or eid,
                             **({"table": e["table"]} if e.get("table") else {"text": e.get("text_read", "")}),
                             "evidence_ref": eid, "decision": p["appendix_why"][eid]})
            disposition.setdefault(eid, "supporting")
            decisions.log(run, stage=STAGE, source="KB", action="route_to_appendix",
                          target={"type": "evidence", "id": eid}, result=f"Appendix {aid}: {p['appendix_why'][eid]}",
                          kb_rules=["SPW-09", "DAT-05"])
    cited_on = {}
    for sl in slides:
        for r in sl.get("evidence", []):
            cited_on.setdefault(r["ref"], sl["id"])
    for a in draft.get("appendix", []):
        refs = [x for x in a.get("evidence", []) if x in by_id]
        aid = f"A{len(appendix) + 1}"
        one = by_id[refs[0]] if len(refs) == 1 else None
        if one and not one.get("table") and _chart_table(one, sources_by_id):
            # The appendix layout draws tables, not charts: give it the chart's own values,
            # cell for cell, rather than a sentence about them.
            one = dict(one, table=_chart_table(one, sources_by_id))
        entry = {"id": aid, "kind": "table" if one and one.get("table") else "evidence",
                 "title": a.get("title") or (one or {}).get("text_live") or "Supporting evidence",
                 "decision": a.get("why", "")}
        if one and one.get("table"):
            entry["table"], entry["evidence_ref"] = one["table"], refs[0]
        else:
            entry["text"] = a.get("text") or " ".join(by_id[x].get("text_read", "") for x in refs)
            if refs:
                entry["evidence_refs"] = refs
        parent = next((cited_on[x] for x in refs if x in cited_on), None)
        if parent:
            entry["parent_claim"] = parent
        appendix.append(entry)
        for x in refs:
            disposition.setdefault(x, "supporting")
        decisions.log(run, stage=STAGE, source="MODEL", action="route_to_appendix",
                      target={"type": "appendix", "id": aid}, result=f"{entry['title']}: {a.get('why', '')}",
                      kb_rules=["SPW-09", "SPW-11"], review_required=True)

    # ---- flags, exclusions, dispositions
    flags = []
    for i, c in enumerate(draft.get("conflicts", []), start=1):
        fid = f"FLAG{i:02d}"
        evs = [x for x in c["evidence"] if x in by_id]
        flags.append({"id": fid, "type": "conflicting_sources", "evidence": evs,
                      "note": f"{c.get('note', '')} Handling in this draft: {c.get('handling', 'not stated')}".strip()})
        for x in evs:
            if by_id[x]["provenance"]["verification"] == "source-supported":
                by_id[x]["provenance"]["verification"] = "disputed"
        decisions.log(run, stage=STAGE, source="MODEL", action="conflict", target={"type": "flag", "id": fid},
                      result=c.get("note", ""), rationale=f"Handling: {c.get('handling', 'not stated')}",
                      review_required=True)
    excluded = {x.get("evidence") for x in draft.get("excluded", []) if x.get("evidence")}
    for x in draft.get("excluded", []):
        tgt = x.get("evidence") or x.get("source") or "?"
        decisions.log(run, stage=STAGE, source="MODEL", action="exclude",
                      target={"type": "evidence" if x.get("evidence") else "source", "id": tgt},
                      result=x.get("why", ""), review_required=True)
    for e in evidence:
        e["disposition"] = disposition.get(e["id"], "excluded" if e["id"] in excluded else "background")

    # ---- figures on slides must trace to evidence
    all_numbers = set().union(*(verify.evidence_numbers(_with_quote(e)) for e in evidence)) if evidence else set()
    slide_figures = _slide_numbers_check(run, slides, by_id, all_numbers, claims)

    spec = {"spec_version": "0.2", "deck_id": deck_id, "status": "draft", "profile": str(run / "profile.json"),
            "deck": {"logo": "Helix Bioworks"}, "thesis": draft["story"]["thesis"], "sections": sections,
            "sources": sources, "evidence": evidence, "claims": claims, "slides": slides, "appendix": appendix,
            "flags": flags}
    errors = validate(spec)
    if errors:
        raise DraftError([f"assembled spec failed schema validation: {e}" for e in errors])
    save_json(run / "deck_spec.json", spec)
    save_json(run / "story.json", {"story": draft["story"], "brief": draft["brief"]})
    _write_gaps(run, deck_id, gaps)

    # ---- QA and rendering
    outputs = _qa_and_render(spec, run, active, profile)
    review = write_review(project, run, draft, spec, intake, issues, slide_figures, outputs)
    return {"deck_id": deck_id, "slides": len(slides), "claim_slides": len(plans), "appendix": len(appendix),
            "evidence": len(evidence), "flags": len(flags), "outputs": outputs, "review": str(review),
            "needs_review": len([d for d in decisions.unreviewed(run) if d["stage"] == STAGE]),
            "unverified_quotes": issues["unverified"], "figures_not_in_source": [x[0] for x in issues["numbers"]],
            "slide_figures_unsupported": [x[0] for x in slide_figures]}


def _qa_and_render(spec, run, active, profile) -> dict:
    from ..kb import load_brand
    from ..qa import lint as lint_mod, report
    from ..render.html_render import render_html
    from ..render.pptx_render import Renderer

    v1 = to_v1_view(spec)
    brand = load_brand(ROOT / BRAND_PATH)
    lf, skipped, exemptions = lint_mod.lint(v1, active, brand, run)
    sev = report.write(run, v1, lf, [], skipped, exemptions,
                       "Deterministic lint only. The model judgment pass was not run in draft mode.")
    renderer = Renderer(ROOT / "template" / "layout_manifest.json", ROOT / "template" / "deck_template.pptx")
    decisions.clear_stage(run, "renderer")
    mode = profile["delivery"]["mode"]
    variants = {"live": ["live"], "read": ["read"], "both": ["live", "read"]}[mode]
    files, overflow = {}, []
    for v in variants:
        name = f"{spec['deck_id']}.pptx" if v == variants[0] else f"{spec['deck_id']}_{v}.pptx"
        out = run / name
        rep = renderer.render(v1, v, str(out), run=run)
        overflow += [f"{x['slide']} ({v}): {'; '.join(x['overflow'])}" for x in rep if x.get("overflow")]
        files[v] = str(out)
    files["html"] = str(render_html(spec, run / f"{spec['deck_id']}.html"))
    return {"files": files, "lint": dict(sev), "overflow": overflow}


# ---------------------------------------------------------------- review

def write_review(project, run, draft, spec, intake, issues, slide_figures, outputs) -> Path:
    story, brief = draft["story"], draft["brief"]
    L = [f"# First draft: {spec['deck_id']}", ""]
    files = outputs["files"]
    L += ["Files:", ""] + [f"- {k}: `{Path(v).relative_to(project)}`" for k, v in files.items()] + [""]
    L += ["## The case", "", f"Audience: {brief['audience']}. Decision-maker: {brief.get('decision_maker', 'not stated')}.",
          "", f"Ask: {brief['ask']}", "", f"Thesis: {story['thesis']}", ""]
    for k, label in (("problem", "Problem"), ("why_care", "Why they care"), ("solution", "Our answer"),
                     ("why_best", "Why it is the best way forward")):
        if story.get(k):
            L.append(f"{label}: {story[k]}")
            L.append("")
    L += ["## Headline flow", "",
          "Read these in order. If the case does not come through from the headlines alone, the draft is not ready.", ""]
    for sl in spec["slides"]:
        if sl["layout"] == "section_divider":
            L.append(f"**{sl['headline']}**")
        else:
            L.append(f"{sl['id'][2:]}. {sl.get('headline', '')}  _({sl['layout']})_")
    L.append("")

    entries = [e for e in decisions.read(run) if e["stage"] == STAGE and e["review_required"]]
    groups = {}
    for e in entries:
        groups.setdefault(e["action"], []).append(e)
    order = [("conflict", "Sources that disagree"), ("quote_not_found", "Quotes not found in their source"),
             ("figure_not_in_source", "Figures not found in their source"),
             ("slide_figure_unsupported", "Figures on slides not in the cited evidence"),
             ("assumption", "Assumptions made where the sources were silent"),
             ("placeholder", "Placeholders to fill"), ("gap", "Gaps in the evidence"),
             ("cut", "Material cut from the main deck"), ("exclude", "Excluded sources and evidence"),
             ("route_to_appendix", "Moved to the appendix")]
    L += ["## Decisions that need you", ""]
    if not entries:
        L += ["None flagged.", ""]
    shown = set()
    for action, title in order + [(a, a.replace("_", " ").capitalize()) for a in groups if a not in dict(order)]:
        if action in shown or action not in groups:
            continue
        shown.add(action)
        L += [f"### {title}", ""]
        for e in groups[action]:
            why = f" {e['rationale']}" if e.get("rationale") else ""
            L.append(f"- **{e['target'].get('id')}** ({e['id']}): {e['result']}{why}")
        L.append("")

    unread = [s for s in intake["sources"] if s["status"] != "read"]
    if unread:
        L += ["## Sources not read", ""] + [f"- {s['filename']} ({s['status']}): {s['note']}" for s in unread] + [""]
    gaps = (run / "layout_gaps.md").read_text().split("\n", 2)[-1].strip()
    L += ["## Layout fit", "", gaps or "No gaps.", ""]
    if outputs["overflow"]:
        L += ["Text the renderer reported as overflowing its box:", ""] + [f"- {x}" for x in outputs["overflow"]] + [""]
    lint = outputs["lint"]
    L += ["## Rule checks", "",
          f"Lint: {lint.get('block', 0)} blocking, {lint.get('warn', 0)} warnings, {lint.get('advise', 0)} advisory. "
          f"Details in `out/qa_report.md`. Rules were not classified against this deck's profile, so some advisories "
          f"may not apply.", "",
          "## Counts", "",
          f"{len(spec['slides'])} slides, {len(spec['evidence'])} evidence items from {len(spec['sources'])} sources, "
          f"{len(spec['appendix'])} appendix items, {len(spec['flags'])} conflict flag(s). Every decision is in "
          f"`out/decision_log.jsonl`.", ""]
    path = project / "REVIEW.md"
    path.write_text("\n".join(L) + "\n")
    (run / "decisions_report.md").write_text(decisions.report(run))
    return path
