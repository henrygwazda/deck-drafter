"""
Milestone 7: native Google Slides rendering of a whole deck (every slide and appendix
page, every layout in the manifest), extending the Milestone 3 vertical slice in
render.py. Option B from docs/GOOGLE_SLIDES_DESIGN.md: each slide is created blank and
every element drawn at manifest geometry, with object ids that are the deck's own ids
(dt_SL04_headline, dt_SL04_EV03_chart_val2).

Layout choice comes from the same LayoutEngine the PowerPoint renderer uses. Charts,
diagrams, and tables are built from shapes, lines, and native tables, so they are
editable in place. Every element the renderer creates is recorded in a meta map:
which slide it belongs to, which spec path or evidence value it shows, so sync can
turn an edit back into a change to the spec.

Request building is pure. create_deck() and read_deck() are the only network calls.
"""
from __future__ import annotations

import time

from ..common import ROOT, load_json, run_dir, save_json
from ..render.layout_engine import LayoutEngine
from .render import (PAGE_H_PT, PAGE_W_PT, _align_paragraph, _alt_text, _create_text_box, _page_background,
                     _style_text, box_pt, chart_bar_requests, hex_to_rgb_float, pt)


def oid(sid: str, name: str) -> str:
    return f"dt_{sid}_{name}"


class Builder:
    """Accumulates requests and element meta for one deck."""

    def __init__(self, engine: LayoutEngine, scale: float):
        self.e, self.scale = engine, scale
        self.reqs: list[dict] = []
        self.meta: dict[str, dict] = {}

    def color(self, key):
        return hex_to_rgb_float(self.e.rgb(key))

    def note(self, object_id, sid, **info):
        self.meta[object_id] = {"slide": sid, **info}

    # ---- primitives (inches in, points out) ----
    def text(self, object_id, page, box_in, text, font="body", size=14, color="slate", bold=False, align="left"):
        x, y, w, h = box_pt(box_in, self.scale)
        self.reqs += [_create_text_box(object_id, page, x, y, max(w, 1), max(h, 1)),
                      {"insertText": {"objectId": object_id, "text": text}},
                      _style_text(object_id, self.e.font(font), size * self.scale, self.color(color), bold),
                      _align_paragraph(object_id, align)]

    def shape(self, object_id, page, kind, box_in, fill, outline=None):
        x, y, w, h = box_pt(box_in, self.scale)
        self.reqs.append({"createShape": {"objectId": object_id, "shapeType": kind, "elementProperties": {
            "pageObjectId": page,
            "size": {"width": {"magnitude": max(w, 1), "unit": "PT"}, "height": {"magnitude": max(h, 1), "unit": "PT"}},
            "transform": {"scaleX": 1, "scaleY": 1, "translateX": x, "translateY": y, "unit": "PT"}}}})
        props = {"shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": self.color(fill)}}}} if fill else \
            {"shapeBackgroundFill": {"propertyState": "NOT_RENDERED"}}
        fields = "shapeBackgroundFill.solidFill.color" if fill else "shapeBackgroundFill.propertyState"
        if outline:
            props["outline"] = {"outlineFill": {"solidFill": {"color": {"rgbColor": self.color(outline)}}},
                                "weight": {"magnitude": 0.75, "unit": "PT"}}
            fields += ",outline.outlineFill.solidFill.color,outline.weight"
        else:
            props["outline"] = {"propertyState": "NOT_RENDERED"}
            fields += ",outline.propertyState"
        self.reqs.append({"updateShapeProperties": {"objectId": object_id, "fields": fields, "shapeProperties": props}})

    def shape_text(self, object_id, text, size=13, color="navy", bold=True):
        self.reqs += [{"insertText": {"objectId": object_id, "text": text}},
                      _style_text(object_id, self.e.font("body"), size * self.scale, self.color(color), bold),
                      _align_paragraph(object_id, "center"),
                      {"updateShapeProperties": {"objectId": object_id, "fields": "contentAlignment",
                                                 "shapeProperties": {"contentAlignment": "MIDDLE"}}}]

    def line(self, object_id, page, x1, y1, x2, y2, color="slate", weight=1.5, dash=None, arrow=False):
        """Inches in. A line runs from its box's origin corner; negative scale flips it,
        so any direction can be drawn."""
        s = self.scale
        dx, dy = pt(x2 - x1, s), pt(y2 - y1, s)
        self.reqs.append({"createLine": {"objectId": object_id, "lineCategory": "STRAIGHT", "elementProperties": {
            "pageObjectId": page,
            "size": {"width": {"magnitude": max(abs(dx), 0.01), "unit": "PT"},
                     "height": {"magnitude": max(abs(dy), 0.01), "unit": "PT"}},
            "transform": {"scaleX": -1 if dx < 0 else 1, "scaleY": -1 if dy < 0 else 1,
                          "translateX": pt(x1, s), "translateY": pt(y1, s), "unit": "PT"}}}})
        props = {"lineFill": {"solidFill": {"color": {"rgbColor": self.color(color)}}},
                 "weight": {"magnitude": weight, "unit": "PT"}}
        fields = "lineFill.solidFill.color,weight"
        if dash:
            props["dashStyle"] = dash
            fields += ",dashStyle"
        if arrow:
            props["endArrow"] = "FILL_ARROW"
            fields += ",endArrow"
        self.reqs.append({"updateLineProperties": {"objectId": object_id, "fields": fields, "lineProperties": props}})

    def table(self, object_id, page, box_in, columns, rows, sid, eid):
        x, y, w, h = box_pt(box_in, self.scale)
        n_rows = len(rows) + 1
        self.reqs.append({"createTable": {"objectId": object_id, "rows": n_rows, "columns": len(columns),
                                          "elementProperties": {"pageObjectId": page,
                                              "size": {"width": {"magnitude": w, "unit": "PT"},
                                                       "height": {"magnitude": min(h, 32 * n_rows * self.scale), "unit": "PT"}},
                                              "transform": {"scaleX": 1, "scaleY": 1, "translateX": x, "translateY": y,
                                                            "unit": "PT"}}}})
        for r, row in enumerate([columns] + rows):
            for c, cell in enumerate(row):
                if not str(cell):
                    continue
                loc = {"rowIndex": r, "columnIndex": c}
                self.reqs += [{"insertText": {"objectId": object_id, "cellLocation": loc, "text": str(cell)}},
                              {"updateTextStyle": {"objectId": object_id, "cellLocation": loc, "textRange": {"type": "ALL"},
                                                   "fields": "fontSize,fontFamily,foregroundColor,bold",
                                                   "style": {"fontSize": {"magnitude": 13 * self.scale, "unit": "PT"},
                                                             "fontFamily": self.e.font("body"),
                                                             "foregroundColor": {"opaqueColor": {"rgbColor": self.color(
                                                                 "navy" if r == 0 or c == 0 else "slate")}},
                                                             "bold": r == 0 or c == 0}}}]
                if r > 0:
                    self.note(f"{object_id}#{r},{c}", sid, role="data", evidence=eid, field="table", row=r - 1, col=c)

    # ---- regions ----
    def line_chart(self, sid, page, box, eid, c):
        x, y, w, h = box
        cats, series = c["categories"], c["series"]
        allv = [v for s in series for v in s["values"]]
        lo, hi = min(allv + [0]), max(allv + [1])
        px = lambda i: x + 0.4 + i * (w - 0.8) / max(1, len(cats) - 1)
        py = lambda v: y + h - 0.45 - (h - 0.9) * ((v - lo) / (hi - lo) if hi > lo else 0.5)
        unit = "%" if c.get("unit") == "%" else ""
        self.line(oid(sid, f"{eid}_axis"), page, x, y + h - 0.45, x + w, y + h - 0.45, color="rule", weight=1)
        for i, cat in enumerate(cats):
            self.text(oid(sid, f"{eid}_chart_cat{i}"), page, (px(i) - 0.5, y + h - 0.4, 1.0, 0.3), cat, size=12, align="center")
        for si, s in enumerate(series):
            col = "navy" if si == 0 else "mist"
            vals = s["values"]
            for i in range(len(vals) - 1):
                self.line(oid(sid, f"{eid}_s{si}_seg{i}"), page, px(i), py(vals[i]), px(i + 1), py(vals[i + 1]),
                          color=col, weight=2.5)
            for i, v in enumerate(vals):
                self.shape(oid(sid, f"{eid}_s{si}_pt{i}"), page, "ELLIPSE", (px(i) - 0.06, py(v) - 0.06, 0.12, 0.12), col)
                if si == 0:
                    vid = oid(sid, f"{eid}_chart_val{i}")
                    self.text(vid, page, (px(i) - 0.45, py(v) - 0.38, 0.9, 0.28), f"{v:g}{unit}", size=12, color=col,
                              bold=True, align="center")
                    self.note(vid, sid, role="data", evidence=eid, field="chart", index=i)

    def scatter(self, sid, page, box, eid, c):
        x, y, w, h = box
        pts = [(px_, py_, si) for si, s in enumerate(c["series"]) for px_, py_ in s["points"]]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        pad = (max(xs) - min(xs)) * 0.1 or 1
        xlo, xhi, ylo, yhi = min(xs) - pad, max(xs) + pad, min(0, min(ys)), max(ys) * 1.1 or 1
        X = lambda v: x + 0.6 + (w - 0.8) * (v - xlo) / (xhi - xlo)
        Y = lambda v: y + h - 0.6 - (h - 0.9) * (v - ylo) / (yhi - ylo)
        self.line(oid(sid, f"{eid}_xaxis"), page, x + 0.55, y + h - 0.6, x + w, y + h - 0.6, color="rule", weight=1)
        self.line(oid(sid, f"{eid}_yaxis"), page, x + 0.55, y + 0.1, x + 0.55, y + h - 0.6, color="rule", weight=1)
        for k, vx in enumerate(sorted(set(xs))[:8]):
            self.text(oid(sid, f"{eid}_xtick{k}"), page, (X(vx) - 0.4, y + h - 0.58, 0.8, 0.25), f"{vx:g}", size=11,
                      align="center")
        for k, vy in enumerate((ylo, yhi / 1.1)):
            self.text(oid(sid, f"{eid}_ytick{k}"), page, (x, Y(vy) - 0.12, 0.5, 0.25), f"{vy:g}", size=11, align="right")
        self.text(oid(sid, f"{eid}_xlabel"), page, (x + 0.6, y + h - 0.32, w - 0.6, 0.3), c.get("x_label", ""), size=12,
                  align="center")
        self.text(oid(sid, f"{eid}_ylabel"), page, (x, y, 1.6, 0.3), c.get("y_label", ""), size=12)
        for k, (vx, vy, si) in enumerate(pts):
            self.shape(oid(sid, f"{eid}_pt{k}"), page, "ELLIPSE" if si == 0 else "RECTANGLE",
                       (X(vx) - 0.07, Y(vy) - 0.07, 0.14, 0.14), "navy" if si == 0 else "mist")
            lid = oid(sid, f"{eid}_pt{k}_label")
            self.text(lid, page, (X(vx) + 0.1, Y(vy) - 0.3, 1.0, 0.26), f"{vx:g}, {vy:g}", size=11)
            self.note(lid, sid, role="data", evidence=eid, field="scatter", index=k)

    def flow(self, sid, page, box, eid, d):
        x, y, w, h = box
        steps, hi = d["steps"], d.get("highlight")
        n, gap = len(steps), 0.4
        bw = (w - gap * (n - 1)) / n
        bh = min(h, 1.6)
        by = y + (h - bh) / 2
        for i, s in enumerate(steps):
            sid_ = oid(sid, f"{eid}_step{i}")
            self.shape(sid_, page, "ROUND_RECTANGLE", (x + i * (bw + gap), by, bw, bh), "navy" if i == hi else "tint")
            self.shape_text(sid_, f"{i + 1:02d}\n{s}", size=14, color="white" if i == hi else "navy")
            self.note(sid_, sid, role="data", evidence=eid, field="diagram_step", index=i)
            if i < n - 1:
                ax = x + i * (bw + gap) + bw
                self.line(oid(sid, f"{eid}_arrow{i}"), page, ax + 0.05, by + bh / 2, ax + gap - 0.05, by + bh / 2,
                          color="mist", weight=2, arrow=True)

    def mechanism(self, sid, page, box, eid, d):
        from ..render.pptx_render import Renderer
        x, y, w, h = box
        layer, forward, back = Renderer._mechanism_layout(d)
        ncols = max(layer.values()) + 1
        cols = {}
        for n in d["nodes"]:
            cols.setdefault(layer[n["id"]], []).append(n)
        gap = 0.6
        bw = min(2.6, (w - gap * (ncols - 1)) / ncols)
        x0 = x + (w - (ncols * bw + (ncols - 1) * gap)) / 2
        lane = 0.45 if back else 0
        notes = {}
        for e in forward:
            if e.get("label"):
                notes.setdefault(e["to"], []).append(e["label"])
        pos = {}
        for ci in range(ncols):
            nodes = cols.get(ci, [])
            bh = min(1.05, (h - lane - 0.3 * (len(nodes) - 1)) / max(1, len(nodes)))
            y0 = y + (h - lane - (len(nodes) * bh + 0.3 * (len(nodes) - 1))) / 2
            for ni, n in enumerate(nodes):
                bx, by = x0 + ci * (bw + gap), y0 + ni * (bh + 0.3)
                on = n["id"] == d.get("highlight")
                nid = oid(sid, f"{eid}_node_{n['id']}")
                self.shape(nid, page, "ROUND_RECTANGLE", (bx, by, bw, bh), "navy" if on else "tint", outline="rule")
                label = n["label"] + "".join(f"\n({t})" for t in notes.get(n["id"], []))
                self.shape_text(nid, label, size=12, color="white" if on else "navy")
                self.note(nid, sid, role="data", evidence=eid, field="mechanism_node", node=n["id"])
                pos[n["id"]] = (bx, by, bw, bh)
        for i, e in enumerate(forward):
            fx, fy, fw, fh = pos[e["from"]]
            tx, ty, tw, th = pos[e["to"]]
            self.line(oid(sid, f"{eid}_edge{i}"), page, fx + fw, fy + fh / 2, tx, ty + th / 2, arrow=True)
        floor = y + h - lane
        for j, e in enumerate(back):
            fx, fy, fw, fh = pos[e["from"]]
            tx, ty, tw, th = pos[e["to"]]
            ly, sx, ex = floor + 0.25, fx + fw / 2, tx + tw / 2
            self.line(oid(sid, f"{eid}_fb{j}a"), page, sx, fy + fh, sx, ly, color="ochre", dash="DASH")
            self.line(oid(sid, f"{eid}_fb{j}b"), page, sx, ly, ex, ly, color="ochre", dash="DASH")
            self.line(oid(sid, f"{eid}_fb{j}c"), page, ex, ly, ex, ty + th, color="ochre", dash="DASH", arrow=True)
            self.text(oid(sid, f"{eid}_fb{j}_label"), page, ((sx + ex) / 2 - 1.2, ly + 0.02, 2.4, 0.25),
                      f"feedback: {e.get('label') or ''}", size=10, align="center")

    def objections(self, sid, page, box, rows):
        x, y, w, h = box
        lw = w * 0.42
        self.text(oid(sid, "risks_head_q"), page, (x, y, lw, 0.3), "Question", size=13, bold=True)
        self.text(oid(sid, "risks_head_r"), page, (x + lw + 0.4, y, w - lw - 0.4, 0.3), "Response", size=13, bold=True)
        rh = (h - 0.42) / max(1, len(rows))
        for i, r in enumerate(rows):
            ry = y + 0.42 + i * rh
            self.line(oid(sid, f"risks_rule{i}"), page, x, ry, x + w, ry, color="rule", weight=1)
            q, a = oid(sid, f"risk_{r['id']}_q"), oid(sid, f"risk_{r['id']}_r")
            self.text(q, page, (x, ry + 0.12, lw, rh - 0.2), r["text"], font="head", size=17, color="navy")
            self.text(a, page, (x + lw + 0.4, ry + 0.14, w - lw - 0.4, rh - 0.2), r.get("mitigation", ""), size=16)
            self.note(q, sid, role="risk", risk=r["id"], field="text")
            self.note(a, sid, role="risk", risk=r["id"], field="mitigation")

    def big_number(self, sid, page, box, e):
        x, y, w, h = box
        eid = e.get("id", "EV")
        self.shape(oid(sid, f"{eid}_panel"), page, "RECTANGLE", box, "tint")
        v, l = oid(sid, f"{eid}_value"), oid(sid, f"{eid}_label")
        value = LayoutEngine._evidence_field(e, "value")
        label = LayoutEngine._evidence_field(e, "label")
        if h < 3:
            self.text(v, page, (x + 0.4, y + 0.2, w * 0.45, h - 0.4), value, font="head", size=54, color="navy")
            self.text(l, page, (x + w * 0.5, y + 0.3, w * 0.45, h - 0.6), label, size=15)
        else:
            self.text(v, page, (x + 0.4, y + 0.4, w - 0.8, 1.4), value, font="head", size=64, color="navy")
            self.text(l, page, (x + 0.4, y + 1.9, w - 0.8, 1.0), label, size=15)
        self.note(v, sid, role="data", evidence=eid, field="value")
        self.note(l, sid, role="data", evidence=eid, field="label")

    def comparison(self, sid, page, box, e):
        x, y, w, h = box
        eid, c = e.get("id", "EV"), e["comparison"]
        half = (h - 0.25) / 2
        for i, side in enumerate(("left", "right")):
            by = y + i * (half + 0.25)
            self.shape(oid(sid, f"{eid}_{side}_panel"), page, "RECTANGLE", (x, by, w, half),
                       "tint" if side == "right" else None, outline=None if side == "right" else "rule")
            self.text(oid(sid, f"{eid}_{side}_label"), page, (x + 0.3, by + 0.15, w - 0.6, 0.3), c[side]["label"],
                      size=13, bold=True, color="navy" if side == "right" else "slate")
            t = oid(sid, f"{eid}_{side}_text")
            self.text(t, page, (x + 0.3, by + 0.55, w - 0.6, half - 0.7), c[side]["text"], font="head", size=17,
                      color="navy" if side == "right" else "slate")
            self.note(t, sid, role="data", evidence=eid, field=f"comparison.{side}.text")

    def region(self, sid, page, p, slide):
        kind, box = p["region"], p["box"]
        e = LayoutEngine.primary(slide)
        eid = e.get("id", "EV")
        c, d = e.get("chart") or {}, e.get("diagram") or {}
        if kind == "chart" and c.get("categories"):
            reqs = chart_bar_requests(self.e, sid, eid, box, c, self.scale)
            self.reqs += reqs
            for i in range(len(c["categories"])):
                self.note(oid(sid, f"{eid}_chart_val{i}"), sid, role="data", evidence=eid, field="chart", index=i)
        elif kind == "line_chart" and c.get("categories"):
            self.line_chart(sid, page, box, eid, c)
        elif kind == "scatter" and c.get("type") == "scatter":
            self.scatter(sid, page, box, eid, c)
        elif kind in ("diagram", "mechanism") and (d.get("nodes") or d.get("steps")):
            (self.mechanism if d.get("nodes") else self.flow)(sid, page, box, eid, d)
        elif kind == "evidence_table" and e.get("table"):
            self.table(oid(sid, f"{eid}_table"), page, box, e["table"]["columns"], e["table"]["rows"], sid, eid)
        elif kind == "table" and slide.get("table"):
            t = slide["table"]
            self.table(oid(sid, "table"), page, box, t["columns"], t["rows"], sid, None)
        elif kind == "objections":
            rows = slide.get("objections") or slide.get("risks") or []
            rows = [{"id": r.get("id", f"R{i}"), "text": r.get("text") or r.get("objection", ""),
                     "mitigation": r.get("mitigation") or r.get("response", "")} for i, r in enumerate(rows)]
            self.objections(sid, page, box, rows)
        elif kind == "evidence" and e:
            k = e.get("kind")
            if k == "chart" and c.get("type") == "scatter":
                self.scatter(sid, page, box, eid, c)
            elif k == "chart" and c.get("categories"):
                (self.line_chart(sid, page, box, eid, c) if c.get("type") == "line"
                 else self.reqs.extend(chart_bar_requests(self.e, sid, eid, box, c, self.scale)))
            elif k == "diagram" and (d.get("nodes") or d.get("steps")):
                (self.mechanism if d.get("nodes") else self.flow)(sid, page, box, eid, d)
            elif k == "table" and e.get("table"):
                self.table(oid(sid, f"{eid}_table"), page, box, e["table"]["columns"], e["table"]["rows"], sid, eid)
            elif k == "comparison" and e.get("comparison"):
                self.comparison(sid, page, box, e)
            else:
                q = LayoutEngine.quote(slide)
                x, y, w, h = box
                if q and h >= 3:
                    self.big_number(sid, page, (x, y, w, h * 0.55), e)
                    qid = q.get("id", "Q")
                    self.text(oid(sid, f"{qid}_quote"), page, (x + 0.3, y + h * 0.6, w - 0.6, h * 0.28),
                              q.get("text_read", ""), font="head", size=15, color="navy")
                else:
                    self.big_number(sid, page, box, e)

    def slide(self, s: dict, index: int, ctx: dict, variant: str, state: dict):
        sid = s["id"]
        page = f"dt_{sid}"
        type_ = s["layout"] if variant == "live" else self.e.man["variant_policy"]["read_map"].get(s["layout"], s["layout"])
        lid, why = self.e.choose(type_, s, ctx, variant, state, 0)
        state["_prev"] = lid
        layout = self.e.layouts[lid]
        self.reqs.append({"createSlide": {"objectId": page, "insertionIndex": index,
                                          "slideLayoutReference": {"predefinedLayout": "BLANK"}}})
        self.meta[page] = {"slide": sid, "role": "slide", "layout": lid, "why": why}
        if layout.get("background") and layout["background"] != "white":
            self.reqs.append(_page_background(page, self.e, layout["background"]))
        for k, d in enumerate(layout.get("decor", [])):
            x, y, w, h = d["box"]
            if d["kind"] == "rect":
                self.shape(oid(sid, f"decor{k}"), page, "RECTANGLE", (x, y, w, h), d.get("fill"), outline=d.get("line"))
            elif d["kind"] == "line":
                self.line(oid(sid, f"decor{k}"), page, x, y, x, y + h, color=d.get("color", "rule"), weight=1)
        for p in layout["placeholders"]:
            if p["role"] == "region":
                self.region(sid, page, p, s)
                continue
            paras = self.e.resolve(p["maps_to"], s, ctx, variant)
            if not paras:
                continue
            object_id = oid(sid, p["name"])
            self.text(object_id, page, p["box"], "\n".join(paras), font=p["font"], size=p["size"], color=p["color"],
                      bold=bool(p.get("bold")), align=p.get("align", "left"))
            self.reqs.append(_alt_text(object_id, f"{sid}/{p['name']}"))
            self.note(object_id, sid, role="text", maps_to=p["maps_to"], placeholder=p["name"])
        return lid, why


def deck_plan(view: dict) -> list[dict]:
    """Slides plus appendix pages, in render order (the same plan pptx_render builds)."""
    plan = [dict(s) for s in view["slides"]]
    positions = {s["id"]: i + 1 for i, s in enumerate(plan)}
    for a in view.get("appendix", []):
        parent = positions.get(a.get("parent_claim"))
        label = f"Appendix {a['id']} | " + (f"Supports slide {parent}" if parent else "Supporting material")
        plan.append({"id": a["id"], "layout": "appendix", "headline": a["title"], "table": a.get("table"), "label": label,
                     "body": {"live": [], "read": "" if a.get("table") else a.get("text", "")},
                     "evidence": [{"role": "primary", "placement": "slide",
                                   "source": (a.get("table") or {}).get("source", "")}]})
    return plan


def build_deck_requests(view: dict, variant: str = "live", scale: float = 1.0, manifest_path=None):
    engine = LayoutEngine(manifest_path or (ROOT / "template" / "layout_manifest.json"))
    b = Builder(engine, scale)
    sections = {s["id"]: (i + 1, s) for i, s in enumerate(view.get("sections", []))}
    state, choices = {}, []
    for i, s in enumerate(deck_plan(view)):
        sec_i, sec = sections.get(s.get("section"), (0, None))
        ctx = {"section": sec, "section_index": sec_i, "deck": view.get("deck", {})}
        lid, why = b.slide(s, i, ctx, variant, state)
        choices.append({"slide": s["id"], "layout": lid, "why": why})
    return b.reqs, b.meta, choices


# ---- reading a deck back ----

def _shape_text(el) -> str:
    runs = el.get("shape", {}).get("text", {}).get("textElements", [])
    return "".join(r["textRun"]["content"] for r in runs if "textRun" in r).rstrip("\n")


def read_deck(presentation: dict) -> tuple[list[str], dict]:
    """Slide order and every element's text, with table cells as '<table id>#r,c'
    pseudo-elements. Alt-text title is kept as a second key (duplicates copy it)."""
    order, elements = [], {}
    for pos, slide in enumerate(presentation.get("slides", [])):
        sid = slide["objectId"]
        order.append(sid)
        for el in slide.get("pageElements", []):
            entry = {"slide": sid, "position": pos, "text": _shape_text(el), "alt": el.get("title", "")}
            elements[el["objectId"]] = entry
            for r, row in enumerate(el.get("table", {}).get("tableRows", [])):
                for c, cell in enumerate(row.get("tableCells", [])):
                    runs = cell.get("text", {}).get("textElements", [])
                    text = "".join(x["textRun"]["content"] for x in runs if "textRun" in x).rstrip("\n")
                    elements[f"{el['objectId']}#{r},{c}"] = {"slide": sid, "position": pos, "text": text, "alt": ""}
    return order, elements


def notes_requests(presentation: dict, view: dict) -> list[dict]:
    """Speaker notes for each slide that has them. A slide's notes shape id is only known
    once the slide exists, so this reads the created presentation. Inserting text at
    speakerNotesObjectId creates the notes shape if it does not exist yet. Notes are not
    part of the sync snapshot: edits made to notes in Slides are not synced back."""
    notes = {f"dt_{s['id']}": s.get("notes", "") for s in view.get("slides", []) if s.get("notes")}
    reqs = []
    for slide in presentation.get("slides", []):
        text = notes.get(slide["objectId"])
        nid = slide.get("slideProperties", {}).get("notesPage", {}).get("notesProperties", {}).get("speakerNotesObjectId")
        if text and nid:
            reqs.append({"insertText": {"objectId": nid, "insertionIndex": 0, "text": text}})
    return reqs


def create_deck(spec_path, title_suffix: str = "") -> dict:
    from .auth import slides_service
    from ..spec import to_v1_view

    spec = load_json(spec_path)
    view = to_v1_view(spec)
    run = run_dir(spec["deck_id"])
    svc = slides_service()
    pres = svc.presentations().create(body={
        "title": f"[deck-tool] {spec['deck_id']}{title_suffix}",
        "pageSize": {"width": {"magnitude": PAGE_W_PT, "unit": "PT"}, "height": {"magnitude": PAGE_H_PT, "unit": "PT"}}}).execute()
    pid = pres["presentationId"]
    pw = pres["pageSize"]["width"]
    scale = (pw["magnitude"] / 12700 if pw.get("unit", "EMU") == "EMU" else pw["magnitude"]) / PAGE_W_PT
    reqs, meta, choices = build_deck_requests(view, scale=scale)
    reqs.append({"deleteObject": {"objectId": pres["slides"][0]["objectId"]}})
    for i in range(0, len(reqs), 400):  # keep each batch well under the API's request-size limit
        svc.presentations().batchUpdate(presentationId=pid, body={"requests": reqs[i:i + 400]}).execute()
    created = svc.presentations().get(presentationId=pid).execute()
    nreqs = notes_requests(created, view)
    if nreqs:
        svc.presentations().batchUpdate(presentationId=pid, body={"requests": nreqs}).execute()
    order, elements = read_deck(created)
    snap = {"presentation_id": pid, "scale": scale, "spec_path": str(spec_path), "slide_order": order,
            "elements": elements, "meta": meta, "layouts": choices, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    save_json(run / "slides_map.json", snap)
    (run / "snapshots").mkdir(exist_ok=True)
    save_json(run / "snapshots" / f"{snap['ts'].replace(':', '')}.json", snap)
    return snap
