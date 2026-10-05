"""
Deck specification v0.2: schema validation, the v0.2-to-v0.1 rendering view, and the
one-time v0.1-to-v0.2 migration helper.

v0.2 moves evidence out of each slide and into a top-level registry (so evidence can
persist independently of whether any slide currently shows it — see docs/BRIEF.md
section 7), adds a source registry and provenance per evidence item, and separates a
claim (the proposition) from a slide's headline (its presentation-language expression).

The existing linter, renderer, and judge (deck_tool/qa/lint.py, deck_tool/render/pptx_render.py,
deck_tool/qa/judge.py) all expect the v0.1 shape: evidence inline per slide, as flat dicts.
to_v1_view() projects a v0.2 spec into that exact shape so none of those three modules need
to change. deck.py calls it right after loading a spec from disk, whenever spec_version is "0.2".
"""
from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

_SCHEMA_PATH = Path(__file__).parent / "schema" / "spec_v0.2.schema.json"

_FLAT_EVIDENCE_KEYS = (
    "value", "label", "text_live", "text_read", "chart", "diagram", "comparison", "table",
    "derived_from", "customer_result", "partner_named", "partner_approved", "stage",
)


def validate(spec: dict) -> list[str]:
    """Validate a v0.2 spec against the JSON Schema. Returns human-readable error strings, [] if valid."""
    schema = json.loads(_SCHEMA_PATH.read_text())
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(spec), key=lambda e: list(e.path))
    return [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors]


def _flat_source_string(prov: dict, sources_by_id: dict) -> str:
    src = sources_by_id.get(prov.get("source_id"))
    if not src:
        return ""
    parts = [src.get("filename") or src.get("note") or src["id"]]
    if prov.get("locator"):
        parts.append(prov["locator"])
    return ", ".join(p for p in parts if p)


def to_v1_view(spec: dict) -> dict:
    """Project a v0.2 spec into the flat, inline-evidence shape lint/render/judge expect.
    Pure and read-only: never mutates the input spec."""
    if spec.get("spec_version") != "0.2":
        return spec

    sources_by_id = {s["id"]: s for s in spec.get("sources", [])}
    evidence_by_id = {e["id"]: e for e in spec.get("evidence", [])}

    def resolve_ref(ref_obj: dict) -> dict:
        ev = dict(evidence_by_id[ref_obj["ref"]])
        ev["role"] = ref_obj.get("role", "supporting")
        ev["placement"] = ref_obj.get("placement", "slide")
        if ref_obj.get("decided_by"):
            ev["decided_by"] = ref_obj["decided_by"]
        prov = ev.pop("provenance", {}) or {}
        ev["source"] = _flat_source_string(prov, sources_by_id)
        if prov.get("verification") == "derived":
            ev["derived"] = True
        ev.pop("disposition", None)
        return ev

    def view_slide(s: dict) -> dict:
        out = dict(s)
        out.pop("claim", None)
        out["evidence"] = [resolve_ref(r) for r in s.get("evidence", [])]
        return out

    def view_appendix(a: dict) -> dict:
        out = dict(a)
        out.pop("evidence_ref", None)
        return out

    skip = {"sources", "evidence", "claims", "slides", "appendix", "flags"}
    view = {k: v for k, v in spec.items() if k not in skip}
    view["slides"] = [view_slide(s) for s in spec["slides"]]
    view["appendix"] = [view_appendix(a) for a in spec.get("appendix", [])]
    return view


def _disposition_for(role: str) -> str:
    return {"primary": "primary", "supporting": "supporting"}.get(role, "background")


def migrate_v1_to_v2(spec_v1: dict) -> dict:
    """One-time migration: lift inline per-slide evidence (v0.1) into the top-level
    sources/evidence/claims registries (v0.2). Deterministic, lossless for the fields
    lint/render/judge read. Does not fix planted errors (e.g. an empty source stays empty)."""
    sources: list[dict] = []
    source_id_by_text: dict[str, str] = {}
    evidence_registry: list[dict] = []
    claims: list[dict] = []
    new_slides: list[dict] = []

    def source_id_for(text: str | None) -> str | None:
        text = (text or "").strip()
        if not text:
            return None
        if text not in source_id_by_text:
            sid = f"SRC{len(sources) + 1:02d}"
            sources.append({"id": sid, "filename": text, "doc_type": "unspecified", "note": ""})
            source_id_by_text[text] = sid
        return source_id_by_text[text]

    for s in spec_v1["slides"]:
        refs = []
        primary_ev_ids = []
        for e in s.get("evidence", []):
            sid = source_id_for(e.get("source"))
            verification = "derived" if e.get("derived") else ("source-supported" if sid else "unverified")
            prov = {"verification": verification}
            if sid:
                prov["source_id"] = sid
            evidence_registry.append({
                "id": e["id"],
                "kind": e["kind"],
                "claim_type": e.get("claim_type"),
                "disposition": _disposition_for(e.get("role", "primary")),
                **{k: e[k] for k in _FLAT_EVIDENCE_KEYS if k in e},
                "provenance": prov,
            })
            ref = {"ref": e["id"], "role": e.get("role", "primary"), "placement": e.get("placement", "slide")}
            if e.get("decided_by"):
                ref["decided_by"] = e["decided_by"]
            refs.append(ref)
            if e.get("role") == "primary":
                primary_ev_ids.append(e["id"])

        new_slide = {k: v for k, v in s.items() if k != "evidence"}
        new_slide["evidence"] = refs
        if primary_ev_ids:
            cid = f"CL{len(claims) + 1:02d}"
            claims.append({"id": cid, "statement": s.get("headline", ""), "evidence": primary_ev_ids})
            new_slide["claim"] = cid
        new_slides.append(new_slide)

    out = {k: v for k, v in spec_v1.items() if k != "slides"}
    out["spec_version"] = "0.2"
    out["sources"] = sources
    out["evidence"] = evidence_registry
    out["claims"] = claims
    out["slides"] = new_slides
    out["appendix"] = spec_v1.get("appendix", [])
    out["flags"] = []
    return out
