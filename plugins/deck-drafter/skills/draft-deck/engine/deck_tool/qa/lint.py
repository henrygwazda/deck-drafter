"""
Deterministic QA. Each check names the KB and/or brand rules it enforces. A check runs
only if at least one of its rules is active for the deck profile and variant, takes its
severity from those rules, and is tagged KB, BRAND, or KB+BRAND so findings can be
traced to the layer that raised them. Judgment rules that can't be counted are left to
judge.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field as dc_field

from .. import decisions
from ..kb import SEV_RANK, max_sev

CLAIM = {"claim_big_number", "claim_chart", "claim_diagram", "claim_comparison",
         "claim_trend", "claim_scatter", "claim_table", "claim_mixed", "claim_mechanism"}
# claim_text is content but not CLAIM: it is the explicit layout for claims whose
# support is reasons rather than a visual proof, so it carries no primary evidence.
CONTENT = CLAIM | {"exec_summary", "claim_text", "alternatives_risks", "portfolio_funnel", "positioning"}
LISTY = {"exec_summary", "alternatives_risks", "portfolio_funnel"}
WORD = re.compile(r"[A-Za-z0-9$\[\]%][\w'$%\[\]\-.,:]*")


@dataclass
class Finding:
    check: str
    severity: str
    source: str
    rules: list
    message: str
    slide: str | None = None
    field: str | None = None
    variants: list = dc_field(default_factory=list)
    fix: str = ""


def words(text) -> int:
    if isinstance(text, list):
        return sum(words(t) for t in text)
    return len(WORD.findall(text or ""))


def joined(x) -> str:
    return " ".join(x) if isinstance(x, list) else (x or "")


def body(slide, v):
    return slide.get("body", {}).get(v, [] if v == "live" else "")


def on_slide_texts(slide, v):
    """Text the audience sees on the slide face for this variant."""
    out = [("headline", slide.get("headline", "")), ("subhead", slide.get("subhead", "")),
           ("body", joined(body(slide, v)))]
    for e in slide.get("evidence", []):
        if e.get("placement", "slide") == "slide":
            out.append((f"evidence {e['id']}", e.get("text_live" if v == "live" else "text_read", "") or ""))
            if e.get("chart", {}).get("title"):
                out.append((f"chart title {e['id']}", e["chart"]["title"]))
    for r in slide.get("risks", []):
        out.append((f"risk {r['id']}", f"{r.get('text', '')} {r.get('mitigation', '')}"))
    return [(f, t) for f, t in out if t]


def all_texts(slide, v):
    return on_slide_texts(slide, v) + ([("notes", slide["notes"])] if slide.get("notes") else [])


def has_number(text) -> bool:
    return bool(re.search(r"\d", text or ""))


# ---------------- KB checks ----------------

def c_headline_assertion(spec, v, p, ctx):
    for s in spec["slides"]:
        h = s.get("headline", "")
        if s["layout"] in CONTENT | {"closing"}:
            if h.strip().endswith("?"):
                yield s["id"], "headline", f"Headline is a question: '{h}'", "State the conclusion as a declarative sentence."
            elif words(h) < 5 and s["layout"] != "closing":
                yield s["id"], "headline", f"Headline reads as a label, not an assertion: '{h}'", "Write the slide's conclusion as a full sentence."


def c_headline_length(spec, v, p, ctx):
    for s in spec["slides"]:
        if len(s.get("headline", "")) > p["max_chars"]:
            yield s["id"], "headline", f"Headline is {len(s['headline'])} characters, likely more than 2 lines", "Trim qualifiers."


def c_live_word_cap(spec, v, p, ctx):
    for s in spec["slides"]:
        if s["layout"] not in CONTENT or s["layout"] in p.get("exempt_layouts", []):
            continue
        b = words(body(s, v)) + sum(words(e.get("text_live", "")) for e in s.get("evidence", [])
                                    if e.get("placement", "slide") == "slide")
        total = sum(words(t) for f, t in on_slide_texts(s, v) if not f.startswith("chart title"))
        if total > p["total_max"]:
            yield s["id"], "body", f"{total} words on the slide face (ceiling {p['total_max']})", "Move detail to notes or the read variant."
        elif b > p["body_max"]:
            yield (s["id"], "body", f"{b} words of body text (target {p['body_max']})",
                   "Cut to fragments. Full sentences belong in the read variant.", "warn")


def c_read_page_words(spec, v, p, ctx):
    for s in spec["slides"]:
        if s["layout"] in CONTENT - {"positioning"}:
            n = words(body(s, v))
            if n < p["min"] or n > p["max"]:
                yield s["id"], "body", f"Read page has {n} words (range {p['min']} to {p['max']})", "Adjust so the page stands alone without bloating."


def c_list_length(spec, v, p, ctx):
    for s in spec["slides"]:
        b = body(s, v)
        if s["layout"] in LISTY and isinstance(b, list) and (len(b) == 2 or len(b) > p["max"]):
            yield s["id"], "body", f"List has {len(b)} items (use {p['min']} to {p['max']})", "Regroup or state as plain text."


def c_sub_points(spec, v, p, ctx):
    for s in spec["slides"]:
        b = body(s, v)
        if s["layout"] in CLAIM and isinstance(b, list) and len(b) > p["max"]:
            yield s["id"], "body", f"{len(b)} sub-points on a claim slide (max {p['max']})", "Split the slide or move points to notes."


def c_nesting(spec, v, p, ctx):
    for s in spec["slides"]:
        b = body(s, v)
        if isinstance(b, list) and any(isinstance(i, list) and any(isinstance(j, list) for j in i) for i in b):
            yield s["id"], "body", "Bullets nest more than one level", "Flatten or split into slides."


def c_sections(spec, v, p, ctx):
    n = len(spec.get("sections", []))
    if not p["min"] <= n <= p["max"]:
        yield None, "sections", f"{n} sections (use {p['min']} to {p['max']})", "Regroup the outline."


def c_core_count(spec, v, p, ctx):
    core = [s for s in spec["slides"] if s["layout"] not in ("title", "section_divider")]
    limit = p["max_read"] if v == "read" else min(p["max_live"], max(1, ctx["profile"]["delivery"].get("time_minutes", 20) // 2))
    if len(core) > limit:
        yield None, "slides", f"{len(core)} core slides for the {v} variant (limit {limit})", "Move lower-priority claims to the appendix."


def c_exec_summary(spec, v, p, ctx):
    if not any(s["layout"] == "exec_summary" for s in spec["slides"][:3]):
        yield None, "slides", "No executive summary in the first 3 slides", "Add one stating the thesis, support, and ask."


def c_closing(spec, v, p, ctx):
    last = spec["slides"][-1]
    if last["layout"] != "closing":
        yield last["id"], "layout", "Deck does not end on a closing slide", "End with the thesis and the ask."
    elif re.search(r"^\s*(questions|thank you|thanks|q\s*&\s*a|discussion)\W*$", last.get("headline", ""), re.I):
        yield last["id"], "headline", f"Closing headline is a placeholder: '{last['headline']}'", "Restate the thesis and the specific ask."


def c_alternatives(spec, v, p, ctx):
    if not any(s["layout"] == "alternatives_risks" for s in spec["slides"]):
        yield None, "slides", "No slide addresses objections or alternatives", "Add an alternatives/risks slide."


def c_risk_mitigation(spec, v, p, ctx):
    for s in spec["slides"]:
        for r in s.get("risks", []):
            if not r.get("mitigation"):
                yield s["id"], f"risk {r['id']}", f"Risk without a mitigation: '{r['text']}'", "Pair it with a response in the same section."


def c_primary_evidence(spec, v, p, ctx):
    for s in spec["slides"]:
        if s["layout"] not in CLAIM:
            continue
        prim = [e for e in s.get("evidence", []) if e.get("role") == "primary" and e.get("placement", "slide") == "slide"]
        if len(prim) != 1:
            yield s["id"], "evidence", f"{len(prim)} primary evidence items on the slide (need exactly 1)", "Keep one proof. Route the rest to notes or appendix."


def c_editorial_trace(spec, v, p, ctx):
    for s in spec["slides"]:
        for e in s.get("evidence", []):
            if not e.get("placement") or not e.get("decided_by"):
                yield s["id"], f"evidence {e['id']}", "Evidence has no recorded placement decision or rule citation", "Run the editorial stage."


def c_visual_evidence(spec, v, p, ctx):
    for s in spec["slides"]:
        if s["layout"] in CLAIM:
            for e in s.get("evidence", []):
                if e.get("role") == "primary" and e.get("kind") == "text":
                    yield s["id"], f"evidence {e['id']}", "Primary evidence is text only", "Use a number, chart, diagram, or image."


def charts(spec):
    for s in spec["slides"]:
        for e in s.get("evidence", []):
            if e.get("chart"):
                yield s, e, e["chart"]


def c_axis_zero(spec, v, p, ctx):
    for s, e, c in charts(spec):
        if c.get("type") in ("bar", "column") and c.get("axis_starts_at_zero") is False:
            yield s["id"], f"chart {e['id']}", "Bar chart axis does not start at zero", "Start the value axis at zero."


def c_pie(spec, v, p, ctx):
    for s, e, c in charts(spec):
        if c.get("type") in ("pie", "donut"):
            yield s["id"], f"chart {e['id']}", f"{c['type'].title()} chart used for comparison", "Use a single stacked bar or sorted horizontal bars."


def c_3d(spec, v, p, ctx):
    for s, e, c in charts(spec):
        if c.get("three_d"):
            yield s["id"], f"chart {e['id']}", "3D chart", "Render flat."


def c_dual_axis(spec, v, p, ctx):
    for s, e, c in charts(spec):
        if c.get("dual_axis"):
            yield s["id"], f"chart {e['id']}", "Dual y-axis chart", "Split into juxtaposed panels."


def c_direct_labels(spec, v, p, ctx):
    for s, e, c in charts(spec):
        if c.get("labels") == "legend" and len(c.get("series", [])) <= 3:
            yield s["id"], f"chart {e['id']}", "Legend used where direct labels would work", "Label series on the chart."


def c_chart_title(spec, v, p, ctx):
    for s, e, c in charts(spec):
        t = (c.get("title") or "").lower()
        if t and (t == s.get("headline", "").lower() or re.search(r"\b(shows|proves|fell|rose|cut|grew|dropped)\b", t)):
            yield s["id"], f"chart {e['id']}", "Chart title carries the interpretation", "Make the chart title neutral (what, when). The headline carries the claim."


def c_derived(spec, v, p, ctx):
    for s in spec["slides"]:
        for e in s.get("evidence", []):
            if e.get("derived") and not e.get("derived_from"):
                yield s["id"], f"evidence {e['id']}", "Derived metric without its source values", "Show the values it was computed from."


def c_tables(spec, v, p, ctx):
    items = [(s["id"], e) for s in spec["slides"] for e in s.get("evidence", [])] + [(a["id"], a) for a in spec.get("appendix", [])]
    for sid, x in items:
        t = x.get("table")
        if t:
            missing = [k for k in ("title", "units", "source") if not t.get(k)]
            if missing:
                yield sid, "table", f"Table missing {', '.join(missing)}", "Tables must stand alone."


def c_source(spec, v, p, ctx):
    for s in spec["slides"]:
        for e in s.get("evidence", []):
            if (e.get("kind") in ("big_number", "chart", "table") or e.get("claim_type")) and not e.get("source"):
                yield s["id"], f"evidence {e['id']}", "Evidence has no source", "Name what was measured, by whom, when."


VAGUE = re.compile(r"\b(significant(ly)?|many|growing|substantial|leading|a lot|numerous|various|traction|momentum)\b", re.I)


def c_vague(spec, v, p, ctx):
    for s in spec["slides"]:
        for f, t in all_texts(s, v):
            m = VAGUE.search(t)
            if m and not has_number(t):
                yield s["id"], f, f"Vague qualifier '{m.group(0).lower()}' with no figure", "Replace with the specific number and its basis."


def c_logo(spec, v, p, ctx):
    n = len(spec["slides"])
    for i, s in enumerate(spec["slides"]):
        if s.get("logo") and i not in (0, n - 1):
            yield s["id"], "logo", "Logo on an interior slide", "Logo on opening and closing slides only."


_ENGINE = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from pathlib import Path
        from ..render.layout_engine import LayoutEngine
        _ENGINE = LayoutEngine(Path(__file__).resolve().parent.parent.parent / "template" / "layout_manifest.json")
    return _ENGINE


def c_layout_fit(spec, v, p, ctx):
    """Brief section 10: content no layout variant can hold is reported, not rendered
    over its boundaries without notice."""
    eng = _engine()
    read_map = eng.man["variant_policy"]["read_map"]
    sections = {s["id"]: s for s in spec.get("sections", [])}
    for s in spec["slides"]:
        type_ = s["layout"] if v == "live" else read_map.get(s["layout"], s["layout"])
        ids = eng.by_type.get(type_)
        if not ids:
            yield s["id"], "layout", f"No {v} layout of type {type_} exists", "Use a layout type the manifest defines."
            continue
        sctx = {"section": sections.get(s.get("section")), "section_index": 1, "deck": spec.get("deck", {})}
        reasons = []
        for lid in ids:
            ok, why = eng.fits(lid, s, sctx, v)
            if ok:
                break
            reasons.append(f"{lid}: {why}")
        else:
            yield (s["id"], "layout", f"No {type_} variant holds this slide's content ({'; '.join(reasons)})",
                   "Split the slide, shorten the text, or move evidence to the appendix.")


KB_CHECKS = [
    ("layout_fit", ["DSN-53", "SPW-19"], c_layout_fit, {}),
    ("headline_assertion", ["CLM-01", "CLM-10"], c_headline_assertion, {}),
    ("headline_length", ["CLM-05"], c_headline_length, {"max_chars": 104}),
    ("live_word_cap", ["DSN-53", "SPW-01"], c_live_word_cap, {"body_max": 30, "total_max": 75, "exempt_layouts": [], "_variants": ["live"]}),
    ("read_page_words", ["SPW-19"], c_read_page_words, {"min": 100, "max": 250, "_variants": ["read"]}),
    ("list_length", ["DSN-05", "NAR-08"], c_list_length, {"min": 3, "max": 5, "_variants": ["live"]}),
    ("sub_points", ["CLM-02"], c_sub_points, {"max": 2, "_variants": ["live"]}),
    ("nesting", ["CLM-33", "DSN-49"], c_nesting, {}),
    ("section_count", ["NAR-08"], c_sections, {"min": 3, "max": 5}),
    ("core_slide_count", ["NAR-49", "DSN-54"], c_core_count, {"max_live": 10, "max_read": 10}),
    ("exec_summary", ["NAR-38"], c_exec_summary, {}),
    ("closing_slide", ["CLM-16", "NAR-18"], c_closing, {}),
    ("alternatives_present", ["NAR-19"], c_alternatives, {}),
    ("risk_mitigation", ["NAR-59"], c_risk_mitigation, {}),
    ("one_primary_evidence", ["SPW-03", "CLM-02"], c_primary_evidence, {}),
    ("editorial_trace", ["EVD-05", "FAIL-15"], c_editorial_trace, {}),
    ("visual_evidence", ["EVD-41"], c_visual_evidence, {}),
    ("axis_zero", ["DAT-22"], c_axis_zero, {}),
    ("pie_chart", ["DAT-09"], c_pie, {}),
    ("chart_3d", ["DAT-11"], c_3d, {}),
    ("dual_axis", ["DAT-24"], c_dual_axis, {}),
    ("direct_labels", ["DAT-15"], c_direct_labels, {}),
    ("chart_title_neutral", ["CLM-12"], c_chart_title, {}),
    ("derived_sources", ["EVD-34"], c_derived, {}),
    ("table_standalone", ["DAT-04"], c_tables, {}),
    ("source_present", ["EVD-03"], c_source, {}),
    ("vague_qualifiers", ["EVD-01", "CLM-07"], c_vague, {}),
    ("logo_placement", ["DSN-52"], c_logo, {}),
]
KB_LINTED = {r for _, rules, _, _ in KB_CHECKS for r in rules}


# ---------------- brand checks ----------------

def b_banned(spec, v, p, ctx):
    terms = ctx["brand"]["vocab"][p["list"]]
    for s in spec["slides"]:
        for f, t in all_texts(s, v):
            low = t.lower()
            for term in terms:
                if term in low:
                    yield s["id"], f, f"Banned {p['list']} term '{term}'", "Rewrite in plain, specific language."


def b_punct(spec, v, p, ctx):
    names = {"!": "exclamation point", "\u2014": "em dash", " \u2013 ": "en dash", ";": "semicolon"}
    for s in spec["slides"]:
        for f, t in all_texts(s, v):
            for ch in p["banned"]:
                if ch in t:
                    yield s["id"], f, f"Contains {names.get(ch, repr(ch))}", "Split into two sentences."


def b_naming(spec, v, p, ctx):
    first_seen = False
    for s in spec["slides"]:
        for f, t in all_texts(s, v):
            if re.search(r"\bHB\b", t):
                yield s["id"], f, "Uses 'HB'", "Write 'Helix Bioworks' or 'Helix Brief' in full."
            if not first_seen and f != "notes":
                m = re.search(r"\bHelix\b(?! Brief)", t)
                if m:
                    first_seen = True
                    if not t[m.start():].startswith("Helix Bioworks"):
                        yield s["id"], f, "First company reference is not 'Helix Bioworks'", "Use the full name on first reference."


def norm(t):
    return re.sub(r"\s+", " ", t).strip().lower()


def b_positioning(spec, v, p, ctx):
    slides = [s for s in spec["slides"] if s["layout"] == p["layout"]]
    if not slides:
        yield None, "slides", "No positioning slide. The North Star statement must appear verbatim in pitch decks.", "Add a positioning slide."
    target = norm(ctx["brand"]["verbatim"]["positioning"])
    for s in slides:
        if norm(joined(body(s, v))) != target:
            yield s["id"], "body", "Positioning statement is not verbatim", "Use the North Star text exactly."


def b_claims(spec, v, p, ctx):
    req_by = p.get("require_by_type") or {t: p.get("require", []) for t in p["claim_types"]}
    for s in spec["slides"]:
        for e in s.get("evidence", []):
            ct = e.get("claim_type")
            if ct not in p["claim_types"]:
                continue
            text = f"{e.get('text_live', '')} {e.get('text_read', '')}"
            for need in req_by.get(ct, []):
                if need == "number" and e.get("kind") in ("chart", "table"):
                    continue  # the figures live in the chart or table data
                ok = has_number(text) if need == "number" else bool(e.get(need))
                if not ok:
                    yield s["id"], f"evidence {e['id']}", f"{ct} claim missing {need.replace('_', ' ')}", "Add it or drop the claim."


def b_customer(spec, v, p, ctx):
    for s in spec["slides"]:
        for e in s.get("evidence", []):
            if e.get("customer_result") and e.get("stage") not in ("preclinical", "active_trial", "post_readout_unpublished", "published"):
                yield s["id"], f"evidence {e['id']}", "Customer result has no claims stage", "Record the stage (brand §9.3)."


def b_partner(spec, v, p, ctx):
    for s in spec["slides"]:
        for e in s.get("evidence", []):
            if e.get("partner_named") and not e.get("partner_approved"):
                yield s["id"], f"evidence {e['id']}", "Partner-named result without recorded approval", "Get approval or anonymize."


def b_colors(spec, v, p, ctx):
    for s, e, c in charts(spec):
        n = sum(1 for x in c.get("series", []) if x.get("color") == "ochre")
        if n > p["max_ochre"]:
            yield s["id"], f"chart {e['id']}", f"{n} Ochre series (max {p['max_ochre']})", "Ink Navy for the proof series, Mist for context."


def b_pie(spec, v, p, ctx):
    for s, e, c in charts(spec):
        if c.get("type") in ("pie", "donut") and c.get("slices", 0) > p["max"]:
            yield s["id"], f"chart {e['id']}", f"Pie with {c['slices']} slices (brand max {p['max']})", "Use bars."


def b_imagery(spec, v, p, ctx):
    for s in spec["slides"]:
        tags = set((s.get("image") or {}).get("tags", []))
        bad = tags & set(p["tags"])
        if bad:
            yield s["id"], "image", f"Banned imagery: {', '.join(sorted(bad))}", "Use a real lab environment or product output."


def b_alt(spec, v, p, ctx):
    for s in spec["slides"]:
        if s.get("image") and not s["image"].get("alt"):
            yield s["id"], "image", "Image has no alt text", "Describe the image."


NUMWORD = re.compile(r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(design partners?|partners?|days?|minutes?|hours?|weeks?|months?|teams?|percent|updates?)\b", re.I)


def b_numerals(spec, v, p, ctx):
    for s in spec["slides"]:
        for f, t in all_texts(s, v):
            m = NUMWORD.search(t)
            if m:
                yield s["id"], f, f"Spelled-out data figure '{m.group(0)}'", "Use numerals for data points."


def b_unreviewed(spec, v, p, ctx):
    if spec.get("status") == "final":
        n = len(decisions.unreviewed(ctx["run"]))
        if n:
            yield None, "status", f"Deck marked final with {n} unreviewed tool decisions", "Run: python3 deck.py review <deck_id>"


def b_exec_open(spec, v, p, ctx):
    for s in spec["slides"]:
        if s["layout"] == "exec_summary":
            t = joined(body(s, "read"))
            if not re.search(r"\b(open|not yet|still|unknown|does not yet|don't yet)\b", t, re.I):
                yield s["id"], "body", "Executive summary does not say what is still open", "Add the open question (brand §9.4)."


BRAND_CHECKS = {"banned_phrases": b_banned, "punctuation": b_punct, "naming": b_naming,
                "positioning_verbatim": b_positioning, "claim_requirements": b_claims,
                "customer_results": b_customer, "partner_approval": b_partner,
                "chart_brand_color": b_colors, "pie_slices": b_pie, "banned_imagery": b_imagery,
                "alt_text": b_alt, "numerals": b_numerals, "unreviewed_decisions": b_unreviewed,
                "exec_summary_open": b_exec_open}


# ---------------- runner ----------------

def _emit(gen, check, sev, source, rules, v, out):
    for item in gen:
        sid, fld, msg, fix = item[:4]
        s = min(sev, item[4], key=lambda x: SEV_RANK[x]) if len(item) > 4 else sev
        out.append(Finding(check=check, severity=s, source=source, rules=rules, message=msg,
                           slide=sid, field=fld, variants=[v], fix=fix))


def lint(spec, active, brand, run):
    findings, skipped, exemptions = [], [], []
    profile = active["profile"]
    ctx = {"profile": profile, "brand": brand, "run": run}
    for v, data in active["variants"].items():
        states, brules = data["rules"], data["brand_rules"]
        relax = {}
        for b in brules.values():
            for check, params in ((b.get("lint") or {}).get("relax") or {}).items():
                relax.setdefault(check, {}).update(params)
                exemptions.append(f"{v}: {check} relaxed by {b['id']} ({params})")
        for cid, rules, fn, defaults in KB_CHECKS:
            if "_variants" in defaults and v not in defaults["_variants"]:
                continue
            live = [r for r in rules if states.get(r, {}).get("status", "inactive") != "inactive"]
            if not live:
                skipped.append(f"{v}: {cid} skipped, rules inactive for this profile ({', '.join(rules)})")
                continue
            sev = max_sev(*[states[r]["severity"] for r in live])
            branded = [b["id"] for r in live for b in states[r].get("brand", [])]
            source = "KB+BRAND" if branded or cid in relax else "KB"
            params = {k: val for k, val in defaults.items() if not k.startswith("_")}
            params.update(relax.get(cid, {}))
            _emit(fn(spec, v, params, ctx), cid, sev, source, live + sorted(set(branded)), v, findings)
        for bid, b in brules.items():
            lt = b.get("lint")
            if not lt or lt.get("check") not in BRAND_CHECKS:
                continue
            _emit(BRAND_CHECKS[lt["check"]](spec, v, lt.get("params", {}), ctx),
                  f"{lt['check']}:{bid}", b["severity"], "BRAND", [bid], v, findings)
    return merge_variants(findings), skipped, sorted(set(exemptions))


def merge_variants(findings):
    merged = {}
    for f in findings:
        key = (f.check, f.slide, f.field, f.message)
        if key in merged:
            merged[key].variants = sorted(set(merged[key].variants + f.variants))
        else:
            merged[key] = f
    return sorted(merged.values(), key=lambda f: (-SEV_RANK[f.severity], f.slide or "", f.check))


def as_dicts(findings):
    return [asdict(f) for f in findings]
