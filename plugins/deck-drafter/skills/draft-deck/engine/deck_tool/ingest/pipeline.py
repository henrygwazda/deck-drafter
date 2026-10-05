"""
Milestone 4 Phase 1 orchestration: read every file in a source directory, extract
evidence per document, detect contradictions across the flattened evidence list, and
assemble a schema-v0.2-valid spec with populated sources/evidence/flags but empty
slides/claims/appendix and thesis="" -- narrative development, outline, slide specs,
and editorial decisions are later stages, not run here.
"""
from __future__ import annotations

from pathlib import Path

from .. import decisions
from ..common import load_json, run_dir, save_json
from ..patterns import pattern_ids
from .contradictions import detect_contradictions
from .extract import extract_evidence
from .inventory import build_sources
from .payloads import STRUCTURED_KINDS, check_payload, source_numbers
from .google import SHORTCUT_EXTENSIONS, fetch as google_fetch
from .readers import KNOWN_EXTENSIONS, read_source


def _candidate_to_evidence(ev_id: str, source_id: str, item: dict) -> dict:
    """Map an extraction candidate into a schema evidence[] entry. disposition is
    always "unverified" here: it describes an editorial relationship to a slide that
    doesn't exist yet in Phase 1 output, not a judgment this stage can make. Distinct
    axis from provenance.verification, which happens to share the same string for an
    unrelated reason (sourcing confidence, not editorial status)."""
    ev = {
        "id": ev_id,
        "kind": item["kind"],
        "claim_type": item.get("claim_type"),
        "disposition": "unverified",
        "text_live": item.get("text_live", ""),
        "text_read": item.get("text_read", ""),
        "provenance": {
            "source_id": source_id,
            "locator": item.get("locator", ""),
            "original_value": item.get("original_value"),
            "unit": item.get("unit", ""),
            "transform": item.get("transform", ""),
            "verification": item.get("verification", "unverified"),
        },
    }
    if item["kind"] in STRUCTURED_KINDS and isinstance(item.get(item["kind"]), dict):
        ev[item["kind"]] = item[item["kind"]]
    if item["kind"] == "big_number":
        for k in ("value", "label"):
            if isinstance(item.get(k), str) and item[k].strip():
                ev[k] = item[k].strip()
    if item.get("information_pattern") in pattern_ids():
        ev["information_pattern"] = item["information_pattern"]
    return ev


def _table_item(doc: dict, table: dict) -> dict:
    """An evidence candidate for a table the file format itself delimits, copied cell
    for cell by the reader rather than transcribed by the model."""
    columns, rows = table["columns"], table["rows"]
    where = f"{doc['filename']} {table['locator']}"
    return {
        "kind": "table", "claim_type": None, "information_pattern": "evidence_matrix",
        "text_live": f"Table: {', '.join(columns)}",
        "text_read": f"{where}: {len(rows)} row(s) with columns {', '.join(columns)}.",
        "locator": table["locator"], "original_value": None, "unit": "", "transform": "",
        "verification": "source-supported",
        "table": {"title": where, "columns": columns, "rows": rows, "source": doc["filename"]},
    }


def _chart_item(doc: dict, chart: dict) -> dict:
    """An evidence candidate for a native chart in a source deck, copied from the
    chart's own data rather than read off its rendering."""
    c = chart["chart"]
    where = f"{doc['filename']} {chart['locator']}"
    names = ", ".join(se["name"] for se in c["series"])
    return {
        "kind": "chart", "claim_type": None,
        "text_live": c.get("title") or f"Chart: {names}",
        "text_read": f"{where}: chart of {names} across {len(c['categories'])} categories"
                     f" ({c['categories'][0]} to {c['categories'][-1]}).",
        "locator": chart["locator"], "original_value": None, "unit": "", "transform": "",
        "verification": "source-supported", "chart": c,
    }


def _verify_payload(run, ev_id: str, item: dict, doc_text: str, known: set[str]) -> dict:
    """Drop a structured payload that fails shape or number checks, keeping the fact as
    plain text. Unsupported numbers are review-required (a figure the source may never
    have stated); a shape problem is a routine downgrade, logged but not queued."""
    result = check_payload(item, doc_text, known)
    if not result["shape"] and not result["unsupported_numbers"]:
        return item
    kind = item["kind"]
    if result["unsupported_numbers"]:
        reason = (f"{kind} payload cites number(s) not found in the source document: "
                  f"{', '.join(result['unsupported_numbers'])}")
    else:
        reason = f"{kind} payload malformed: {'; '.join(result['shape'])}"
    decisions.log(run, stage="ingestion", source="MODEL", action="payload_rejected",
                  target={"type": "evidence", "id": ev_id},
                  result=f"{reason}. Payload removed; kept as text so the fact itself is not lost.",
                  review_required=bool(result["unsupported_numbers"]))
    item = {k: v for k, v in item.items() if k != kind}
    item["kind"] = "text"
    return item


def ingest(profile_path, source_dir, google_refs=(), drive=None) -> dict:
    """google_refs: Google Docs/Slides/Sheets URLs or ids, read through Drive alongside
    the files in source_dir. Shortcut files (.gdoc, .gslides, .gsheet) in source_dir are
    read the same way. drive: an injected Drive service, for tests."""
    profile = load_json(profile_path)
    deck_id = profile["deck_id"]
    run = run_dir(deck_id)

    entries = sorted(Path(source_dir).iterdir()) if source_dir else []
    paths = [p for p in entries if p.suffix.lower() in KNOWN_EXTENSIONS]
    refs = [str(p) for p in entries if p.suffix.lower() in SHORTCUT_EXTENSIONS] + list(google_refs)
    raw_docs = [read_source(p) for p in paths]
    if refs:
        if drive is None:
            from ..gslides.auth import drive_service
            drive = drive_service()
        raw_docs += [google_fetch(drive, ref, run / "google_exports") for ref in refs]
    sources = build_sources(raw_docs)
    decisions.clear_stage(run, "ingestion")

    evidence = []
    n = 0
    for source, doc in zip(sources, raw_docs):
        known = source_numbers(doc["text"])
        # Model candidates are checked; reader-captured tables and charts are copied
        # from the file itself, so there is nothing model-introduced to check.
        candidates = [(item, True) for item in extract_evidence(source, doc["text"])]
        candidates += [(_table_item(doc, t), False) for t in doc.get("tables", [])]
        candidates += [(_chart_item(doc, c), False) for c in doc.get("charts", [])]
        for item, from_model in candidates:
            n += 1
            ev_id = f"EV{n:02d}"
            if from_model:
                item = _verify_payload(run, ev_id, item, doc["text"], known)
            evidence.append(_candidate_to_evidence(ev_id, source["id"], item))

    flags = detect_contradictions(evidence, sources)
    for i, flag in enumerate(flags):
        flag.setdefault("id", f"FLAG{i + 1:02d}")
        decisions.log(run, stage="ingestion", source="MODEL", action="flag_contradiction",
                      target={"type": "flag", "id": flag["id"]}, result=flag.get("note", ""),
                      rationale=f"Evidence involved: {', '.join(flag.get('evidence', []))}",
                      review_required=True)

    spec = {
        "spec_version": "0.2", "deck_id": deck_id, "status": "ingested", "profile": str(profile_path),
        "thesis": "", "sections": [], "sources": sources, "evidence": evidence, "claims": [],
        "slides": [], "appendix": [], "flags": flags,
    }

    counts = {s["id"]: 0 for s in sources}
    for e in evidence:
        counts[e["provenance"]["source_id"]] += 1
    report_lines = [
        f"# Ingestion report: {deck_id}", "",
        f"{len(sources)} source(s), {len(evidence)} evidence item(s), {len(flags)} flag(s).", "",
        "| Source | Doc type | Evidence items |", "|---|---|---|",
    ]
    report_lines += [f"| {s['filename']} ({s['id']}) | {s['doc_type']} | {counts[s['id']]} |" for s in sources]
    if flags:
        report_lines += ["", "## Flags needing review", ""]
        report_lines += [f"- **{f.get('id')}** ({f['type']}): {f.get('note', '')} "
                          f"— evidence: {', '.join(f.get('evidence', []))}" for f in flags]
    report_lines.append("")

    save_json(run / "ingested_spec.json", spec)
    (run / "ingestion_report.md").write_text("\n".join(report_lines))
    return spec
