"""
Milestone 3 spike: prove the native Google Slides loop on a tiny deck.

    python3 -m deck_tool.gslides.spike requests          print the batchUpdate requests, no network
    python3 -m deck_tool.gslides.spike create            create a 2-slide deck, save the mapping
    python3 -m deck_tool.gslides.spike readback          read the deck back and report what changed

Acceptance test (from the brief): create, edit a headline by hand in Slides, run readback,
and confirm the edit is matched to the right internal ID. Then also reorder a slide, delete
an element, and add a new text box, and confirm readback classifies each correctly.

Object IDs are chosen by us at creation time, so the internal ID IS the Slides object ID.
Slides requires IDs of 5 to 50 characters matching [a-zA-Z0-9_][a-zA-Z0-9_\\-:]*, hence the prefix.
Pure functions (build_requests, read_elements, diff) are unit tested offline. The network
calls in create() and readback() have NOT been executed as of this commit.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from ..common import ROOT

ID_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_\-:]{4,49}$")
STATE = ROOT / "projects" / "spike" / "slides_map.json"
PAGE_W_PT, PAGE_H_PT = 960.0, 540.0          # 13.333 x 7.5 in at 72 pt/in, same canvas as the manifest

SLIDES = [
    {"id": "SL01", "elements": [
        {"name": "headline", "text": "Spike slide one: edit this headline in Google Slides", "box": [0.75, 0.9, 11.8, 1.2], "size": 30},
        {"name": "body", "text": "Body text for slide one.", "box": [0.75, 2.6, 11.8, 1.2], "size": 20}]},
    {"id": "SL02", "elements": [
        {"name": "headline", "text": "Spike slide two", "box": [0.75, 0.9, 11.8, 1.2], "size": 30},
        {"name": "body", "text": "Body text for slide two.", "box": [0.75, 2.6, 11.8, 1.2], "size": 20}]},
]


def slide_oid(sid): return f"dt_{sid}"
def element_oid(sid, name): return f"dt_{sid}_{name}"


def build_requests(slides=SLIDES, scale=1.0, delete_first=None):
    """Pure: the batchUpdate requests that create the deck. scale maps manifest points to the real page size."""
    reqs = []
    for i, s in enumerate(slides):
        reqs.append({"createSlide": {"objectId": slide_oid(s["id"]), "insertionIndex": i,
                                     "slideLayoutReference": {"predefinedLayout": "BLANK"}}})
        for e in s["elements"]:
            oid = element_oid(s["id"], e["name"])
            x, y, w, h = (v * 72 * scale for v in e["box"])
            reqs += [
                {"createShape": {"objectId": oid, "shapeType": "TEXT_BOX", "elementProperties": {
                    "pageObjectId": slide_oid(s["id"]),
                    "size": {"width": {"magnitude": w, "unit": "PT"}, "height": {"magnitude": h, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": x, "translateY": y, "unit": "PT"}}}},
                {"insertText": {"objectId": oid, "text": e["text"]}},
                {"updateTextStyle": {"objectId": oid, "textRange": {"type": "ALL"}, "fields": "fontSize,fontFamily",
                                     "style": {"fontSize": {"magnitude": e["size"] * scale, "unit": "PT"}, "fontFamily": "Arial"}}},
                {"updatePageElementAltText": {"objectId": oid, "title": f"deck-tool:{s['id']}/{e['name']}",
                                              "description": f"deck-tool:{s['id']}/{e['name']}"}},
            ]
    if delete_first:
        reqs.append({"deleteObject": {"objectId": delete_first}})
    return reqs


def element_text(el):
    runs = el.get("shape", {}).get("text", {}).get("textElements", [])
    return "".join(r["textRun"]["content"] for r in runs if "textRun" in r).rstrip("\n")


def read_elements(presentation):
    """Pure: flatten a presentations.get response into slide order and a dict of elements by objectId."""
    order, elements = [], {}
    for pos, slide in enumerate(presentation.get("slides", [])):
        sid = slide["objectId"]
        order.append(sid)
        for el in slide.get("pageElements", []):
            elements[el["objectId"]] = {"slide": sid, "position": pos, "text": element_text(el),
                                        "alt": el.get("title", "")}
    return order, elements


def diff(snapshot, order, elements):
    """Pure: compare the saved snapshot with the current deck. Returns a list of change dicts."""
    changes = []
    snap_slides = snapshot["slide_order"]
    kept = [s for s in snap_slides if s in order]
    for s in snap_slides:
        if s not in order:
            changes.append({"type": "slide_deleted", "id": s})
    for s in order:
        if s not in snap_slides:
            changes.append({"type": "slide_added_or_duplicated", "id": s, "review": True})
    if kept != [s for s in order if s in snap_slides]:
        changes.append({"type": "slides_reordered", "from": kept, "to": [s for s in order if s in snap_slides]})
    for oid, old in snapshot["elements"].items():
        cur = elements.get(oid)
        if cur is None:
            if old["slide"] in order:
                changes.append({"type": "element_deleted", "id": oid})
            continue
        if cur["slide"] != old["slide"]:
            changes.append({"type": "element_moved_slide", "id": oid, "from": old["slide"], "to": cur["slide"]})
        if cur["text"] != old["text"]:
            changes.append({"type": "text_edited", "id": oid, "from": old["text"], "to": cur["text"],
                            "review": oid.endswith("_headline")})   # headline edits may change the claim
    for oid, cur in elements.items():
        if oid not in snapshot["elements"]:
            changes.append({"type": "element_added", "id": oid, "slide": cur["slide"], "text": cur["text"], "review": True})
    return changes


def create():
    from .auth import slides_service
    svc = slides_service()
    pres = svc.presentations().create(body={"title": "[deck-tool spike] safe to delete",
                                            "pageSize": {"width": {"magnitude": PAGE_W_PT, "unit": "PT"},
                                                         "height": {"magnitude": PAGE_H_PT, "unit": "PT"}}}).execute()
    pid = pres["presentationId"]
    pw = pres["pageSize"]["width"]
    width_pt = pw["magnitude"] / 12700 if pw.get("unit", "EMU") == "EMU" else pw["magnitude"]
    scale = width_pt / PAGE_W_PT      # 1.0 if Slides honored the 960 x 540 pt page size, otherwise scale to fit
    default_slide = pres["slides"][0]["objectId"]
    svc.presentations().batchUpdate(presentationId=pid, body={"requests": build_requests(scale=scale, delete_first=default_slide)}).execute()
    order, elements = read_elements(svc.presentations().get(presentationId=pid).execute())
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"presentation_id": pid, "scale": scale, "slide_order": order, "elements": elements}, indent=2))
    print(f"created https://docs.google.com/presentation/d/{pid}/edit\nmapping saved to {STATE}")


def readback():
    from .auth import slides_service
    snap = json.loads(STATE.read_text())
    order, elements = read_elements(slides_service().presentations().get(presentationId=snap["presentation_id"]).execute())
    changes = diff(snap, order, elements)
    print(json.dumps(changes, indent=2) if changes else "no changes since the snapshot")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "requests":
        print(json.dumps(build_requests(), indent=2))
    elif cmd == "create":
        create()
    elif cmd == "readback":
        readback()
    else:
        print(__doc__)
