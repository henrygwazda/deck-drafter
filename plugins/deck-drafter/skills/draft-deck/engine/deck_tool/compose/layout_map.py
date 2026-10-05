"""
Deterministic layout-type assignment from an approved narrative's arguments and
unresolved questions to slides -- no model call. Evidence-kind-to-layout mapping is
mechanical and auditable: pick the most visually-concrete evidence kind an argument
cites, map it to the matching claim_* layout. table-kind evidence never becomes a
slide's primary evidence (no live claim_table layout exists); it routes to an
appendix entry instead of being flattened into prose. Counterarguments, qualifications,
and unresolved questions merge onto one alternatives_risks slide, collapsing rows that
cite the identical evidence set (the same underlying gap stated twice).
"""
from __future__ import annotations

_KIND_PRIORITY = ["chart", "comparison", "big_number", "diagram", "table", "text"]
_KIND_TO_LAYOUT = {
    "chart": "claim_chart",
    "comparison": "claim_comparison",
    "big_number": "claim_big_number",
    "diagram": "claim_diagram",
}
# chart/comparison/diagram/table layouts bind to a structured sub-object with this same
# name on the evidence item (e.g. evidence.primary.comparison.left.label). Phase 1
# extraction only ever produces flat text_live/text_read, never these structured
# payloads, so a "comparison"-kind item from ingestion typically has no "comparison"
# key at all. Treating the bare kind label as usable without checking the structure
# actually exists crashes the renderer (bracket access, not .get()) on the read variant.
# Only trust a structured kind when its payload is genuinely present; otherwise fall
# through the priority order as if that evidence item didn't carry that kind.
_KIND_REQUIRES_FIELD = {"chart": "chart", "comparison": "comparison", "diagram": "diagram", "table": "table"}


def _usable_kind(ev: dict) -> str | None:
    """The evidence item's kind, or None if that kind requires a structured payload
    (chart/comparison/diagram/table) the item doesn't actually carry."""
    kind = ev["kind"]
    required_field = _KIND_REQUIRES_FIELD.get(kind)
    if required_field and not ev.get(required_field):
        return None
    return kind


def _best_kind(evidence_ids, evidence_by_id):
    present = {_usable_kind(evidence_by_id[eid]) for eid in evidence_ids if eid in evidence_by_id}
    present.discard(None)
    for kind in _KIND_PRIORITY:
        if kind in present:
            return kind
    return None


def assign_layouts(narrative: dict, evidence_by_id: dict) -> dict:
    """Returns {"slides": [...], "risks_slide": {...} | None, "appendix": [...], "folded": [...]}.

    Each slide plan: {source_type:"argument", arg_id, role, layout, primary_evidence, other_evidence}.
    appendix entries: [{"evidence_id", "parent_arg_id"}].
    folded: supporting arguments with no visually-concrete evidence, to be merged into
    another slide's content by the content-generation stage rather than given their own slide.
    risks_slide: {"source_type":"risks", "layout":"alternatives_risks", "groups": [[item,...], ...]}
    where each item is {"kind":"argument"|"question", "id", "evidence": set, "source": dict}.
    """
    slide_plans = []
    appendix_entries = []
    folded = []

    main_args = [a for a in narrative["arguments"] if a["role"] in ("primary", "supporting")]
    risk_args = [a for a in narrative["arguments"] if a["role"] in ("counterargument", "qualification")]

    for arg in main_args:
        evidence_ids = arg.get("evidence", [])
        # Same guard as _usable_kind elsewhere: a table-kind item with no structured
        # table payload (the Phase 1 extraction gap) isn't usable as a table either --
        # routing it to the appendix would write appendix[].table: null, which fails
        # schema validation (confirmed by a real run's table-kind evidence with no
        # payload). Let it fall through to _best_kind's normal priority resolution instead.
        table_ids = [eid for eid in evidence_ids
                     if eid in evidence_by_id and _usable_kind(evidence_by_id[eid]) == "table"]
        for eid in table_ids:
            appendix_entries.append({"evidence_id": eid, "parent_arg_id": arg["id"]})
        non_table_ids = [eid for eid in evidence_ids if eid not in table_ids]

        kind = _best_kind(non_table_ids, evidence_by_id)
        if kind is None or kind == "text":
            # No concrete (non-text) evidence to anchor a claim_* slide. Folded regardless
            # of role -- "positioning" is not a generic prose slot, it's the brand's
            # locked North Star statement (lint's b_positioning requires an exact verbatim
            # match), so it must never be repurposed as a fallback for narrative content.
            folded.append(arg)
            continue
        else:
            layout = _KIND_TO_LAYOUT[kind]
            primary_evidence = next(eid for eid in non_table_ids if _usable_kind(evidence_by_id[eid]) == kind)
            other_evidence = [eid for eid in non_table_ids if eid != primary_evidence]

        slide_plans.append({
            "source_type": "argument", "arg_id": arg["id"], "role": arg["role"],
            "layout": layout, "primary_evidence": primary_evidence, "other_evidence": other_evidence,
        })

    risk_items = [{"kind": "argument", "id": a["id"], "evidence": set(a.get("evidence", [])), "source": a}
                  for a in risk_args]
    risk_items += [{"kind": "question", "id": q["id"], "evidence": set(q.get("evidence", [])), "source": q}
                   for q in narrative.get("unresolved_questions", [])]

    merged_groups, used = [], set()
    for item in risk_items:
        if item["id"] in used:
            continue
        group = [item]
        used.add(item["id"])
        for other in risk_items:
            if other["id"] in used:
                continue
            if item["evidence"] and item["evidence"] == other["evidence"]:
                group.append(other)
                used.add(other["id"])
        merged_groups.append(group)

    risks_slide = {"source_type": "risks", "layout": "alternatives_risks", "groups": merged_groups} \
        if merged_groups else None

    return {"slides": slide_plans, "risks_slide": risks_slide, "appendix": appendix_entries, "folded": folded}
