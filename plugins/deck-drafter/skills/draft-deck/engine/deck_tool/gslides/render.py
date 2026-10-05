"""
Milestone 3: native Google Slides rendering for a vertical slice of the real demo
deck (title, exec_summary, claim_big_number, claim_chart — SL01/SL02/SL04/SL05),
built on the same deck_tool.render.layout_engine.LayoutEngine the PowerPoint renderer
uses, so both renderers make identical layout-selection and capacity decisions for
the same slide.

Charts render as shape-built horizontal bars (plain rectangles sized to value), not a
Sheets-linked chart — no second Google API surface, no linked-file state to track,
fully editable in place like everything else here. Not data-driven: editing a bar's
underlying number in Slides does not move the bar, the same gap as any other evidence
edit until sync handles it.

Request-building (this module's core) is pure and offline. create()/readback() are
the only functions here that touch the network.
"""
from __future__ import annotations

from ..common import ROOT, run_dir, save_json
from ..render.layout_engine import LayoutEngine
from .spike import element_oid, read_elements, slide_oid

PAGE_W_PT, PAGE_H_PT = 960.0, 540.0

SLIDE_IDS = ["SL01", "SL02", "SL04", "SL05"]

ALIGN = {"left": "START", "center": "CENTER", "right": "END"}

CHART_LABEL_COL_IN = 2.3
CHART_VALUE_COL_IN = 0.9
CHART_GAP_RATIO = 0.55


def pt(inches: float, scale: float = 1.0) -> float:
    return inches * 72 * scale


def box_pt(box_in, scale):
    x, y, w, h = box_in
    return pt(x, scale), pt(y, scale), pt(w, scale), pt(h, scale)


def hex_to_rgb_float(hex_str: str) -> dict:
    hex_str = hex_str.lstrip("#")
    r, g, b = (int(hex_str[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return {"red": r, "green": g, "blue": b}


def _create_text_box(object_id, page_id, x, y, w, h):
    return {"createShape": {"objectId": object_id, "shapeType": "TEXT_BOX", "elementProperties": {
        "pageObjectId": page_id,
        "size": {"width": {"magnitude": w, "unit": "PT"}, "height": {"magnitude": h, "unit": "PT"}},
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": x, "translateY": y, "unit": "PT"}}}}


def _style_text(object_id, font_family, font_size_pt, color_rgb, bold=False):
    return {"updateTextStyle": {"objectId": object_id, "textRange": {"type": "ALL"},
            "fields": "fontSize,fontFamily,foregroundColor,bold",
            "style": {"fontSize": {"magnitude": font_size_pt, "unit": "PT"}, "fontFamily": font_family,
                      "foregroundColor": {"opaqueColor": {"rgbColor": color_rgb}}, "bold": bold}}}


def _align_paragraph(object_id, align):
    return {"updateParagraphStyle": {"objectId": object_id, "textRange": {"type": "ALL"},
            "fields": "alignment", "style": {"alignment": ALIGN.get(align, "START")}}}


def _alt_text(object_id, tag):
    return {"updatePageElementAltText": {"objectId": object_id, "title": f"deck-tool:{tag}", "description": f"deck-tool:{tag}"}}


def _page_background(page_id, engine: LayoutEngine, color_key):
    color = hex_to_rgb_float(engine.rgb(color_key))
    return {"updatePageProperties": {"objectId": page_id, "fields": "pageBackgroundFill",
            "pageProperties": {"pageBackgroundFill": {"solidFill": {"color": {"rgbColor": color}}}}}}


def text_placeholder_requests(engine: LayoutEngine, slide_id, name, box_in, font_key, size, color_key,
                               align, bold, text, scale):
    """The request sequence for one manifest text placeholder with resolved content."""
    oid = element_oid(slide_id, name)
    page_id = slide_oid(slide_id)
    x, y, w, h = box_pt(box_in, scale)
    color = hex_to_rgb_float(engine.rgb(color_key))
    return [
        _create_text_box(oid, page_id, x, y, w, h),
        {"insertText": {"objectId": oid, "text": text}},
        _style_text(oid, engine.font(font_key), size * scale, color, bold),
        _align_paragraph(oid, align),
        _alt_text(oid, f"{slide_id}/{name}"),
    ]


def chart_bar_requests(engine: LayoutEngine, slide_id, evidence_id, box_in, chart, scale):
    """Shape-built horizontal bars for a single-series chart dict. Categories top-to-bottom
    in spec order -- a shape-built chart has no bottom-up default to compensate for, unlike
    the PowerPoint renderer's native chart object, so no reverse_order equivalent here."""
    categories = chart["categories"]
    values = chart["series"][0]["values"]
    highlight = set(chart.get("highlight", []))
    unit = chart.get("unit")
    page_id = slide_oid(slide_id)
    x, y, w, h = box_pt(box_in, scale)
    label_col_w = pt(CHART_LABEL_COL_IN, scale)
    value_col_w = pt(CHART_VALUE_COL_IN, scale)
    bar_area_w = w - label_col_w - value_col_w
    n = len(categories)
    bar_thickness = h / (n + CHART_GAP_RATIO * (n - 1))
    gap = CHART_GAP_RATIO * bar_thickness
    max_val = max(values) if values else 1
    navy = hex_to_rgb_float(engine.rgb("navy"))
    mist = hex_to_rgb_float(engine.rgb("mist"))
    slate = hex_to_rgb_float(engine.rgb("slate"))
    body_font = engine.font("body")

    reqs = []
    for i, (cat, val) in enumerate(zip(categories, values)):
        y_i = y + i * (bar_thickness + gap)
        bar_len = bar_area_w * (val / max_val) if max_val else 0.0
        bar_x = x + label_col_w
        bar_oid = element_oid(slide_id, f"{evidence_id}_chart_bar{i}")
        color = navy if (not highlight or i in highlight) else mist  # no highlight: all bars carry the proof

        reqs.append({"createShape": {"objectId": bar_oid, "shapeType": "RECTANGLE", "elementProperties": {
            "pageObjectId": page_id,
            "size": {"width": {"magnitude": max(bar_len, 1.0), "unit": "PT"}, "height": {"magnitude": bar_thickness, "unit": "PT"}},
            "transform": {"scaleX": 1, "scaleY": 1, "translateX": bar_x, "translateY": y_i, "unit": "PT"}}}})
        reqs.append({"updateShapeProperties": {"objectId": bar_oid,
            "fields": "shapeBackgroundFill.solidFill.color,outline.propertyState",
            "shapeProperties": {"shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": color}}},
                                 "outline": {"propertyState": "NOT_RENDERED"}}}})

        cat_oid = element_oid(slide_id, f"{evidence_id}_chart_cat{i}")
        reqs += [
            _create_text_box(cat_oid, page_id, x, y_i, label_col_w - pt(0.1, scale), bar_thickness),
            {"insertText": {"objectId": cat_oid, "text": cat}},
            _style_text(cat_oid, body_font, 13 * scale, slate),
            _align_paragraph(cat_oid, "right"),
        ]

        val_oid = element_oid(slide_id, f"{evidence_id}_chart_val{i}")
        val_text = f"{val:g}%" if unit == "%" else f"{val:g}"
        val_color = navy if (not highlight or i in highlight) else slate
        reqs += [
            _create_text_box(val_oid, page_id, bar_x + bar_len + pt(0.08, scale), y_i,
                              value_col_w - pt(0.08, scale), bar_thickness),
            {"insertText": {"objectId": val_oid, "text": val_text}},
            _style_text(val_oid, body_font, 13 * scale, val_color, bold=True),
        ]
    return reqs


def build_slide_requests(engine: LayoutEngine, spec_view: dict, slide_id: str, insertion_index: int,
                          variant: str, scale: float) -> list[dict]:
    """All requests for one slide: createSlide, every non-empty text placeholder, and the
    chart region if this layout's type has one. Pure, no network."""
    slides_by_id = {s["id"]: s for s in spec_view["slides"]}
    sections = {s["id"]: (i + 1, s) for i, s in enumerate(spec_view.get("sections", []))}
    slide = slides_by_id[slide_id]
    sec_i, sec = sections.get(slide.get("section"), (0, None))
    ctx = {"section": sec, "section_index": sec_i, "deck": spec_view.get("deck", {})}

    state = {}
    layout_id, _ = engine.choose(slide["layout"], slide, ctx, variant, state, 0)
    layout = engine.layouts[layout_id]
    page_id = slide_oid(slide_id)

    reqs = [{"createSlide": {"objectId": page_id, "insertionIndex": insertion_index,
                              "slideLayoutReference": {"predefinedLayout": "BLANK"}}}]
    if layout.get("background") and layout["background"] != "white":
        reqs.append(_page_background(page_id, engine, layout["background"]))

    for p in layout["placeholders"]:
        if p["role"] == "text":
            paras = engine.resolve(p["maps_to"], slide, ctx, variant)
            if not paras:
                continue
            reqs += text_placeholder_requests(engine, slide_id, p["name"], p["box"], p["font"], p["size"],
                                               p["color"], p.get("align", "left"), bool(p.get("bold")),
                                               "\n".join(paras), scale)
        elif p["role"] == "region" and p.get("region") == "chart":
            ev = LayoutEngine.primary(slide)
            if ev.get("chart"):
                reqs += chart_bar_requests(engine, slide_id, ev.get("id", "EV"), p["box"], ev["chart"], scale)

    return reqs


def build_requests(spec_view: dict, manifest_path=None, variant: str = "live", scale: float = 1.0,
                    delete_first: str | None = None) -> list[dict]:
    """Pure: the full batchUpdate request list for the vertical slice."""
    manifest_path = manifest_path or (ROOT / "template" / "layout_manifest.json")
    engine = LayoutEngine(manifest_path)
    reqs = []
    for i, slide_id in enumerate(SLIDE_IDS):
        reqs += build_slide_requests(engine, spec_view, slide_id, i, variant, scale)
    if delete_first:
        reqs.append({"deleteObject": {"objectId": delete_first}})
    return reqs


def create(spec_path, run_id: str | None = None):
    from .auth import slides_service
    from ..common import load_json

    spec = load_json(spec_path)
    from ..spec import to_v1_view
    view = to_v1_view(spec)
    deck_id = run_id or spec["deck_id"]
    run = run_dir(deck_id)

    svc = slides_service()
    pres = svc.presentations().create(body={"title": f"[deck-tool] {deck_id} (Milestone 3 vertical slice)",
                                            "pageSize": {"width": {"magnitude": PAGE_W_PT, "unit": "PT"},
                                                         "height": {"magnitude": PAGE_H_PT, "unit": "PT"}}}).execute()
    pid = pres["presentationId"]
    pw = pres["pageSize"]["width"]
    width_pt = pw["magnitude"] / 12700 if pw.get("unit", "EMU") == "EMU" else pw["magnitude"]
    scale = width_pt / PAGE_W_PT
    default_slide = pres["slides"][0]["objectId"]

    reqs = build_requests(view, variant="live", scale=scale, delete_first=default_slide)
    svc.presentations().batchUpdate(presentationId=pid, body={"requests": reqs}).execute()
    order, elements = read_elements(svc.presentations().get(presentationId=pid).execute())

    save_json(run / "slides_map.json", {"presentation_id": pid, "scale": scale, "slide_order": order, "elements": elements})
    print(f"created https://docs.google.com/presentation/d/{pid}/edit\nmapping saved to {run / 'slides_map.json'}")


def readback(run_id: str):
    from .auth import slides_service
    from ..common import load_json
    from .spike import diff

    run = run_dir(run_id)
    snap = load_json(run / "slides_map.json")
    if not snap:
        raise SystemExit(f"No slides_map.json for {run_id}. Run: python3 deck.py gslides create <spec.json>")
    order, elements = read_elements(slides_service().presentations().get(presentationId=snap["presentation_id"]).execute())
    return diff(snap, order, elements)
