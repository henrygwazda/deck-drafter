"""
Human editorial overrides that persist across re-composes. A reviewer promotes an
appendix item back into the live deck (or demotes a live risk row) at first-draft
review; the choice is written to runs/<deck_id>/editorial_overrides.json and logged
as a HUMAN decision, and every later compose honours it. Overrides are keyed by the
narrative item id (UQ03, ARG07), not by appendix position, which changes per run.
"""
from __future__ import annotations

from .. import decisions
from ..common import load_json, run_dir, save_json
from .pipeline import OVERRIDES_FILE


def _write(run, key: str, item_id: str, other: str):
    data = load_json(run / OVERRIDES_FILE, {}) or {}
    data.setdefault(key, [])
    data.setdefault(other, [])
    if item_id not in data[key]:
        data[key].append(item_id)
    data[other] = [x for x in data[other] if x != item_id]
    save_json(run / OVERRIDES_FILE, data)


def promote(deck_id: str, appendix_id: str) -> str:
    run = run_dir(deck_id)
    spec = load_json(run / "deck_spec.json")
    if not spec:
        raise SystemExit(f"No composed deck for {deck_id}. Run: python3 deck.py compose {deck_id}")
    entry = next((a for a in spec["appendix"] if a["id"] == appendix_id), None)
    if entry is None:
        raise SystemExit(f"No appendix entry {appendix_id} in {deck_id}. Entries: "
                         f"{', '.join(a['id'] for a in spec['appendix']) or 'none'}")
    if entry.get("kind") != "risk":
        rows = len((entry.get("table") or {}).get("rows", []))
        raise SystemExit(f"{appendix_id} is supporting evidence ({rows} table rows), kept in the appendix because no "
                         f"live layout holds it within its capacity. It stays available in the expanded HTML version. "
                         f"Only risk entries can be promoted for now.")
    item_id = entry["source_ref"]
    _write(run, "promote", item_id, "demote")
    decisions.log(run, stage="human_override", source="HUMAN", action="promote_to_live",
                  target={"type": "risk", "id": item_id},
                  result=f"{item_id} ({appendix_id}) moved back onto the live risks slides by a reviewer.",
                  review_required=False)
    return item_id


def demote(deck_id: str, item_id: str) -> str:
    run = run_dir(deck_id)
    _write(run, "demote", item_id, "promote")
    decisions.log(run, stage="human_override", source="HUMAN", action="demote_to_appendix",
                  target={"type": "risk", "id": item_id},
                  result=f"{item_id} moved from the live risks slides to the appendix by a reviewer.",
                  review_required=False)
    return item_id
