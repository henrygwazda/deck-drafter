"""
Renders a deck spec into the PowerPoint template, one file per delivery variant.

Layouts come from the template's slide master, chosen by the manifest's variant policy:
cycle through a type's variants, skip one that repeats the previous slide's layout, and
skip one whose placeholders can't hold the slide's text. Text is written into named
placeholders with formatting taken from the manifest, so the output looks the same in
PowerPoint and after a Google Slides import. Regions (charts, diagrams, tables) are drawn
as native shapes and charts inside the region's box. Every shape the renderer fills is
named '<slide_id>/<placeholder>' for the sync step.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION
from pptx.enum.shapes import MSO_SHAPE, PP_PLACEHOLDER
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from .. import decisions
from .layout_engine import LayoutEngine, split_halves  # noqa: F401 (split_halves re-exported for callers)

ALIGN = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}
ANCHOR = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE, "bottom": MSO_ANCHOR.BOTTOM}


class Renderer:
    def __init__(self, manifest_path, template_path):
        self.engine = LayoutEngine(manifest_path)
        self.man = self.engine.man
        self.template_path = template_path
        self.layouts = self.engine.layouts
        self.by_type = self.engine.by_type
        self.colors = self.engine.colors
        self.fonts = self.engine.fonts

    # ---------- helpers ----------
    def rgb(self, name):
        return RGBColor.from_string(self.engine.rgb(name))

    def font(self, key):
        return self.engine.font(key)

    # ---------- spec resolution ----------
    @staticmethod
    def primary(slide):
        return LayoutEngine.primary(slide)

    def resolve(self, path, slide, ctx, variant):
        return self.engine.resolve(path, slide, ctx, variant)

    # ---------- variant choice ----------
    def fits(self, layout_id, slide, ctx, variant):
        return self.engine.fits(layout_id, slide, ctx, variant)

    def choose(self, type_, slide, ctx, variant, state, offset):
        return self.engine.choose(type_, slide, ctx, variant, state, offset)

    # ---------- text ----------
    def write_text(self, shape, paras, p):
        tf = shape.text_frame
        tf.clear()
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.NONE
        for side in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
            setattr(tf, side, 0)
        tf.vertical_anchor = ANCHOR.get(p.get("valign", "top"), MSO_ANCHOR.TOP)
        for i, text in enumerate(paras):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            pPr = para._p.get_or_add_pPr()
            for child in list(pPr):
                if child.tag in (qn("a:buChar"), qn("a:buAutoNum"), qn("a:buNone")):
                    pPr.remove(child)
            pPr.set("marL", "0")
            pPr.set("indent", "0")
            pPr.insert(0, pPr.makeelement(qn("a:buNone"), {}))
            para.alignment = ALIGN.get(p.get("align", "left"))
            para.line_spacing = p.get("line_spacing", 1.2)
            if len(paras) > 1:
                para.space_after = Pt(p["size"] * 0.55)
            run = para.add_run()
            run.text = text
            f = run.font
            f.name = self.font(p["font"])
            f.size = Pt(p["size"])
            f.bold = bool(p.get("bold"))
            f.color.rgb = self.rgb(p["color"])

    def textbox(self, slide, box, text, font="body", size=14, color="slate", bold=False, align="left",
                anchor="top", name=None, line=1.15):
        x, y, w, h = box
        tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        self.write_text(tb, text if isinstance(text, list) else [text],
                        {"font": font, "size": size, "color": color, "bold": bold, "align": align,
                         "valign": anchor, "line_spacing": line})
        if name:
            tb.name = name
        return tb

    def rect(self, slide, box, fill, name=None, line=None):
        x, y, w, h = box
        r = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
        r.shadow.inherit = False
        if fill:
            r.fill.solid()
            r.fill.fore_color.rgb = self.rgb(fill)
        else:
            r.fill.background()
        if line:
            r.line.color.rgb = self.rgb(line)
            r.line.width = Pt(0.75)
        else:
            r.line.fill.background()
        if name:
            r.name = name
        return r

    def hline(self, slide, x, y, w, color="rule", name=None):
        ln = slide.shapes.add_connector(1, Inches(x), Inches(y), Inches(x + w), Inches(y))
        ln.line.color.rgb = self.rgb(color)
        ln.line.width = Pt(1)
        if name:
            ln.name = name
        return ln

    # ---------- regions ----------
    def draw_region(self, slide, kind, box, spec_slide, sid, variant):
        e = self.primary(spec_slide)
        eid = e.get("id", "region")
        if kind == "chart" and e.get("chart"):
            self.chart(slide, box, e["chart"], f"{sid}/{eid}/chart")
        elif kind == "diagram" and e.get("diagram"):
            self.diagram(slide, box, e["diagram"], f"{sid}/{eid}/diagram")
        elif kind == "objections":
            rows = spec_slide.get("objections") or [{"objection": r["text"], "response": r.get("mitigation", "")}
                                                    for r in spec_slide.get("risks", [])]
            self.objections(slide, box, rows, f"{sid}/objections")
        elif kind == "stages" and e.get("stages"):
            self.stages(slide, box, e["stages"], f"{sid}/{eid}/stages")
        elif kind == "table" and spec_slide.get("table"):
            self.table(slide, box, spec_slide["table"], f"{sid}/table")
        elif kind == "evidence":
            q = LayoutEngine.quote(spec_slide)
            x, y, w, h = box
            if q and e.get("kind") == "big_number" and h >= 3:
                # Mixed evidence on a read page: figure above, quote below, so the
                # qualitative half is not lost when the live layout maps to read_claim.
                self.evidence(slide, (x, y, w, h * 0.55), e, f"{sid}/{eid}")
                self.textbox(slide, (x + 0.3, y + h * 0.6, w - 0.6, h * 0.28), q.get("text_read", ""), font="head",
                             size=16, color="navy", line=1.3, name=f"{sid}/{q.get('id', 'quote')}/quote")
                self.textbox(slide, (x + 0.3, y + h * 0.9, w - 0.6, h * 0.1), q.get("source", ""), size=11,
                             name=f"{sid}/{q.get('id', 'quote')}/quote_source")
            else:
                self.evidence(slide, box, e, f"{sid}/{eid}")
        elif kind == "line_chart" and (e.get("chart") or {}).get("categories"):
            self.line_chart(slide, box, e["chart"], f"{sid}/{eid}/chart")
        elif kind == "scatter" and (e.get("chart") or {}).get("type") == "scatter":
            self.scatter(slide, box, e["chart"], f"{sid}/{eid}/chart")
        elif kind == "evidence_table" and e.get("table"):
            self.table(slide, box, e["table"], f"{sid}/{eid}/table")
        elif kind == "mechanism" and e.get("diagram"):
            if e["diagram"].get("nodes"):
                self.mechanism(slide, box, e["diagram"], f"{sid}/{eid}/mechanism")
            elif e["diagram"].get("steps"):
                self.diagram(slide, box, e["diagram"], f"{sid}/{eid}/diagram")

    def chart(self, slide, box, c, name):
        x, y, w, h = box
        cd = CategoryChartData()
        cd.categories = c["categories"]
        fmt = '0"%"' if c.get("unit") == "%" else "General"
        for s in c["series"]:
            cd.add_series(s["name"], s["values"], number_format=fmt)
        horizontal = c.get("orientation") == "horizontal"
        ctype = XL_CHART_TYPE.BAR_CLUSTERED if horizontal else XL_CHART_TYPE.COLUMN_CLUSTERED
        gf = slide.shapes.add_chart(ctype, Inches(x), Inches(y), Inches(w), Inches(h), cd)
        gf.name = name
        ch = gf.chart
        ch.has_legend = False
        ch.has_title = False
        ch.font.name = self.font("body")
        ch.font.size = Pt(13)
        ch.font.color.rgb = self.rgb("slate")
        plot = ch.plots[0]
        plot.gap_width = 55 if horizontal else 80
        plot.overlap = -15 if len(c["series"]) > 1 else 0
        va, ca = ch.value_axis, ch.category_axis
        va.minimum_scale = 0
        va.has_major_gridlines = False
        va.visible = False
        ca.has_major_gridlines = False
        ca.format.line.color.rgb = self.rgb("rule")
        ca.tick_labels.font.size = Pt(13)
        ca.tick_labels.font.color.rgb = self.rgb("slate")
        if horizontal:
            ca.reverse_order = True
        hi = set(c.get("highlight", []))
        multi = len(c["series"]) > 1
        for si, s in enumerate(ch.series):
            spec_s = c["series"][si]
            proof = spec_s.get("role", "proof") == "proof"
            for pi, _ in enumerate(spec_s["values"]):
                pt = s.points[pi]
                pt.format.fill.solid()
                if multi:
                    col = "navy" if proof else "mist"
                else:
                    col = "navy" if (not hi or pi in hi) else "mist"
                pt.format.fill.fore_color.rgb = self.rgb(col)
                dl = pt.data_label
                dl.position = XL_LABEL_POSITION.OUTSIDE_END
                val = spec_s["values"][pi]
                txt = f"{val:g}%" if c.get("unit") == "%" else f"{val:g}"
                tf = dl.text_frame
                tf.text = txt
                for run in tf.paragraphs[0].runs:
                    run.font.size = Pt(13)
                    run.font.bold = True
                    run.font.name = self.font("body")
                    run.font.color.rgb = self.rgb("navy" if col == "navy" else "slate")
        if multi:
            # direct series labels above the plot, aligned left, instead of a detached legend
            lx = x
            for si, spec_s in enumerate(c["series"]):
                col = "navy" if spec_s.get("role", "proof") == "proof" else "mist"
                self.rect(slide, (lx, y - 0.02, 0.16, 0.16), col, name=f"{name}/key{si}")
                tb = self.textbox(slide, (lx + 0.24, y - 0.09, 3.0, 0.3), spec_s["name"], size=13, color="slate",
                                  name=f"{name}/label{si}")
                lx += 0.24 + 0.11 * len(spec_s["name"]) + 0.45
            gf.top = Inches(y + 0.3)
            gf.height = Inches(h - 0.3)

    def _style_axis(self, axis, visible=True):
        axis.has_major_gridlines = False
        axis.visible = visible
        axis.format.line.color.rgb = self.rgb("rule")
        axis.tick_labels.font.size = Pt(12)
        axis.tick_labels.font.color.rgb = self.rgb("slate")

    def line_chart(self, slide, box, c, name):
        """DAT-07 line chart for a trend, DAT-45 marked points, DAT-41 start and end values
        labelled. The proof series is navy, context series mist."""
        x, y, w, h = box
        cd = CategoryChartData()
        cd.categories = c["categories"]
        for se in c["series"]:
            cd.add_series(se["name"], se["values"])
        gf = slide.shapes.add_chart(XL_CHART_TYPE.LINE_MARKERS, Inches(x), Inches(y), Inches(w), Inches(h), cd)
        gf.name = name
        ch = gf.chart
        ch.has_title = False
        ch.has_legend = len(c["series"]) > 1
        ch.font.name = self.font("body")
        ch.font.size = Pt(12)
        ch.font.color.rgb = self.rgb("slate")
        self._style_axis(ch.category_axis)
        va = ch.value_axis
        self._style_axis(va)
        va.has_major_gridlines = True
        va.major_gridlines.format.line.color.rgb = self.rgb("rule")
        unit = c.get("unit", "")
        for si, se in enumerate(ch.series):
            col = "navy" if si == 0 or c["series"][si].get("role") == "proof" else "mist"
            se.smooth = False
            se.format.line.color.rgb = self.rgb(col)
            se.format.line.width = Pt(2.5)
            se.marker.style = 8  # circle
            se.marker.size = 7
            se.marker.format.fill.solid()
            se.marker.format.fill.fore_color.rgb = self.rgb(col)
            se.marker.format.line.color.rgb = self.rgb(col)
            vals = c["series"][si]["values"]
            for pi in {0, len(vals) - 1}:
                dl = se.points[pi].data_label
                dl.position = XL_LABEL_POSITION.ABOVE
                dl.text_frame.text = f"{vals[pi]:g}{'%' if unit == '%' else ''}"
                for run in dl.text_frame.paragraphs[0].runs:
                    run.font.size = Pt(13)
                    run.font.bold = True
                    run.font.color.rgb = self.rgb(col)

    def scatter(self, slide, box, c, name):
        """DAT-32 scatterplot for the relationship between two continuous variables."""
        from pptx.chart.data import XyChartData
        x, y, w, h = box
        cd = XyChartData()
        for se in c["series"]:
            s_ = cd.add_series(se["name"])
            for px, py in se["points"]:
                s_.add_data_point(px, py)
        gf = slide.shapes.add_chart(XL_CHART_TYPE.XY_SCATTER, Inches(x), Inches(y), Inches(w), Inches(h), cd)
        gf.name = name
        ch = gf.chart
        ch.has_title = False
        ch.has_legend = len(c["series"]) > 1
        ch.font.name = self.font("body")
        ch.font.size = Pt(12)
        ch.font.color.rgb = self.rgb("slate")
        for axis, label in ((ch.category_axis, c.get("x_label", "")), (ch.value_axis, c.get("y_label", ""))):
            self._style_axis(axis)
            if label:
                axis.has_title = True
                axis.axis_title.text_frame.text = label
                for run in axis.axis_title.text_frame.paragraphs[0].runs:
                    run.font.size = Pt(12)
                    run.font.bold = False
                    run.font.color.rgb = self.rgb("slate")
        xs = [px for se in c["series"] for px, _ in se["points"]]
        if xs and min(xs) > 0:
            # No zero baseline is implied by position, so start the x axis just below the
            # data instead of wasting most of the plot (DAT-23 allows this off bar charts).
            lo, hi_ = min(xs), max(xs)
            step = 10 ** math.floor(math.log10(max(hi_ - lo, 1e-9))) / 2 if hi_ > lo else 1
            ch.category_axis.minimum_scale = math.floor((lo - step) / step) * step
            ch.category_axis.maximum_scale = math.ceil((hi_ + step) / step) * step
        styles = (8, 1, 2)  # circle, square, diamond: a shape cue beside colour (DAT-32)
        for si, se in enumerate(ch.series):
            col = "navy" if si == 0 else "mist"
            se.format.line.fill.background()
            se.marker.style = styles[si % 3]
            se.marker.size = 9
            se.marker.format.fill.solid()
            se.marker.format.fill.fore_color.rgb = self.rgb(col)
            se.marker.format.line.color.rgb = self.rgb(col)

    @staticmethod
    def _mechanism_layout(d):
        """Columns by longest path over forward edges; edges that point back to an
        earlier column (feedback) are returned separately so they can be drawn dashed."""
        ids = [n["id"] for n in d["nodes"]]
        order = {nid: i for i, nid in enumerate(ids)}
        forward, back = [], []
        for e in d.get("edges", []):
            if e.get("from") not in order or e.get("to") not in order:
                continue
            if e.get("kind") == "feedback" or order[e["to"]] <= order[e["from"]]:
                back.append(e)
            else:
                forward.append(e)
        layer = {nid: 0 for nid in ids}
        for nid in ids:  # ids are in input order; forward edges always point later
            for e in forward:
                if e["from"] == nid:
                    layer[e["to"]] = max(layer[e["to"]], layer[nid] + 1)
        return layer, forward, back

    def _arrow_end(self, connector, kind):
        ln = connector.line._get_or_add_ln()
        tail = ln.makeelement(qn("a:tailEnd"), {"type": "oval" if kind == "inhibits" else "triangle",
                                                "w": "med", "len": "med"})
        ln.append(tail)

    def mechanism(self, slide, box, d, name):
        """Nodes in columns, causal edges as arrows, inhibition with a round end, and
        feedback edges dashed and routed beneath the diagram so they never cross a node
        (DSN-39 simplified line art). An edge label is shown as a small second line in
        its target node, where it cannot collide with other shapes."""
        from pptx.enum.dml import MSO_LINE_DASH_STYLE
        from pptx.enum.shapes import MSO_CONNECTOR
        x, y, w, h = box
        layer, forward, back = self._mechanism_layout(d)
        ncols = max(layer.values()) + 1
        cols = {}
        for n in d["nodes"]:
            cols.setdefault(layer[n["id"]], []).append(n)
        gap_x = 0.6
        bw = min(2.6, (w - gap_x * (ncols - 1)) / ncols)
        span = ncols * bw + (ncols - 1) * gap_x
        x0 = x + (w - span) / 2
        lane = 0.45 if back else 0.0  # room beneath the nodes for feedback paths
        # Type scales with box width so a long word never breaks mid-word in a narrow region.
        longest = max((len(wd) for n in d["nodes"] for wd in n["label"].split()), default=8)
        node_pt = max(9, min(14, int((bw - 0.2) * 72 / (0.62 * longest))))
        hi = d.get("highlight")
        notes = {}
        for e in forward + back:
            if e.get("label") and e in forward:
                notes.setdefault(e["to"], []).append(e["label"])
        pos = {}
        for ci in range(ncols):
            nodes = cols.get(ci, [])
            avail = h - lane
            bh = min(1.05, (avail - 0.3 * (len(nodes) - 1)) / max(1, len(nodes)))
            total = len(nodes) * bh + 0.3 * (len(nodes) - 1)
            y0 = y + (avail - total) / 2
            for ni, n in enumerate(nodes):
                bx, by = x0 + ci * (bw + gap_x), y0 + ni * (bh + 0.3)
                on = n["id"] == hi
                shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(bx), Inches(by), Inches(bw), Inches(bh))
                shp.shadow.inherit = False
                shp.fill.solid()
                shp.fill.fore_color.rgb = self.rgb("navy" if on else "tint")
                shp.line.color.rgb = self.rgb("navy" if on else "rule")
                shp.name = f"{name}/node/{n['id']}"
                self.write_text(shp, [n["label"]], {"font": "body", "size": node_pt, "color": "white" if on else "navy",
                                                    "bold": True, "align": "center", "valign": "middle"})
                tf = shp.text_frame
                for side in ("margin_left", "margin_right"):
                    setattr(tf, side, Inches(0.08))
                for note in notes.get(n["id"], []):
                    para = tf.add_paragraph()
                    para.alignment = PP_ALIGN.CENTER
                    run = para.add_run()
                    run.text = f"({note})"
                    run.font.size = Pt(max(8, node_pt - 3))
                    run.font.italic = True
                    run.font.name = self.font("body")
                    run.font.color.rgb = self.rgb("mist" if on else "slate")
                pos[n["id"]] = (bx, by, bw, bh)

        for i, e in enumerate(forward):
            fx, fy, fw, fh = pos[e["from"]]
            tx, ty, tw, th = pos[e["to"]]
            cn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(fx + fw), Inches(fy + fh / 2),
                                            Inches(tx), Inches(ty + th / 2))
            cn.line.color.rgb = self.rgb("slate")
            cn.line.width = Pt(1.5)
            self._arrow_end(cn, e.get("kind"))
            cn.name = f"{name}/edge/{i + 1}"
        floor = y + h - lane
        for j, e in enumerate(back):
            fx, fy, fw, fh = pos[e["from"]]
            tx, ty, tw, th = pos[e["to"]]
            ly = floor + 0.25 + 0.1 * j
            sx, ex = fx + fw / 2, tx + tw / 2
            fb = slide.shapes.build_freeform(Inches(sx), Inches(fy + fh))
            fb.add_line_segments([(Inches(sx), Inches(ly)), (Inches(ex), Inches(ly)), (Inches(ex), Inches(ty + th))],
                                 close=False)
            path = fb.convert_to_shape()
            path.fill.background()
            path.shadow.inherit = False
            path.line.color.rgb = self.rgb("ochre")
            path.line.width = Pt(1.5)
            path.line.dash_style = MSO_LINE_DASH_STYLE.DASH
            self._arrow_end(path, e.get("kind"))
            path.name = f"{name}/feedback/{j + 1}"
            label = e.get("label") or "feedback"
            self.textbox(slide, ((sx + ex) / 2 - 1.2, ly + 0.02, 2.4, 0.25), f"feedback: {label}", size=10,
                         color="slate", align="center", name=f"{name}/feedback/{j + 1}/label")

    def diagram(self, slide, box, d, name):
        x, y, w, h = box
        steps, hi = d["steps"], d.get("highlight")
        n = len(steps)
        vertical = h > w * 0.5
        gap = 0.45
        for i, label in enumerate(steps):
            on = i == hi
            if vertical:
                bh = (h - gap * (n - 1)) / n
                bx, by, bw = x, y + i * (bh + gap), w
                bhh = bh
            else:
                bw = (w - gap * (n - 1)) / n
                bhh = min(h, 1.9)
                bx, by = x + i * (bw + gap), y + (h - bhh) / 2
            self.rect(slide, (bx, by, bw, bhh), "navy" if on else "tint", name=f"{name}/step{i + 1}")
            self.textbox(slide, (bx + 0.22, by + 0.18, bw - 0.44, 0.3), f"{i + 1:02d}", size=12, bold=True,
                         color="mist" if on else "slate", name=f"{name}/step{i + 1}/index")
            ty = by + 0.52 if not vertical else by + 0.15
            tx = bx + 0.22 if not vertical else bx + 0.8
            self.textbox(slide, (tx, ty, bw - (0.44 if not vertical else 1.0), bhh - (0.65 if not vertical else 0.25)),
                         label, font="head", size=20, color="white" if on else "navy",
                         anchor="top" if not vertical else "middle", name=f"{name}/step{i + 1}/label")
            if i < n - 1:
                if vertical:
                    a = slide.shapes.add_shape(MSO_SHAPE.DOWN_ARROW, Inches(bx + 0.25), Inches(by + bhh + 0.07),
                                               Inches(0.24), Inches(gap - 0.14))
                else:
                    a = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(bx + bw + 0.08), Inches(by + bhh / 2 - 0.12),
                                               Inches(gap - 0.16), Inches(0.24))
                a.fill.solid()
                a.fill.fore_color.rgb = self.rgb("mist")
                a.line.fill.background()
                a.shadow.inherit = False
                a.name = f"{name}/arrow{i + 1}"

    def objections(self, slide, box, rows, name):
        x, y, w, h = box
        lw = w * 0.42
        self.textbox(slide, (x, y, lw, 0.3), "Question", size=13, bold=True, name=f"{name}/head1")
        self.textbox(slide, (x + lw + 0.4, y, w - lw - 0.4, 0.3), "Response", size=13, bold=True, name=f"{name}/head2")
        top = y + 0.42
        rh = (h - 0.42) / max(1, len(rows))
        for i, r in enumerate(rows):
            ry = top + i * rh
            self.hline(slide, x, ry, w, name=f"{name}/rule{i + 1}")
            self.textbox(slide, (x, ry + 0.14, lw, rh - 0.2), r["objection"], font="head", size=18, color="navy",
                         name=f"{name}/row{i + 1}/question")
            self.textbox(slide, (x + lw + 0.4, ry + 0.16, w - lw - 0.4, rh - 0.2), r["response"], size=17,
                         color="slate", line=1.25, name=f"{name}/row{i + 1}/response")

    def stages(self, slide, box, stages, name):
        x, y, w, h = box
        n, gap = len(stages), 0.35
        cw = (w - gap * (n - 1)) / n
        for i, s in enumerate(stages):
            cx = x + i * (cw + gap)
            self.rect(slide, (cx, y, cw, h), "tint" if i < n - 1 else "navy", name=f"{name}/stage{i + 1}")
            last = i == n - 1
            self.textbox(slide, (cx + 0.25, y + 0.3, cw - 0.5, 1.2), s.get("value", ""), font="head", size=40,
                         color="white" if last else "navy", name=f"{name}/stage{i + 1}/value")
            self.textbox(slide, (cx + 0.25, y + 1.55, cw - 0.5, h - 1.8), s.get("label", ""), size=16,
                         color="mist" if last else "slate", name=f"{name}/stage{i + 1}/label")

    def table(self, slide, box, t, name):
        x, y, w, h = box
        cols = t["columns"]
        weights = [1.4] + [1.0] * (len(cols) - 1)
        widths = [w * k / sum(weights) for k in weights]
        rh = min(0.6, (h - 0.5) / max(1, len(t["rows"])))
        cx = x
        for ci, col in enumerate(cols):
            self.textbox(slide, (cx, y, widths[ci] - 0.2, 0.5), col, size=13, bold=True, anchor="bottom",
                         name=f"{name}/head{ci + 1}")
            cx += widths[ci]
        for ri, row in enumerate(t["rows"]):
            ry = y + 0.6 + ri * rh
            self.hline(slide, x, ry, w, name=f"{name}/rule{ri + 1}")
            cx = x
            for ci, cell in enumerate(row):
                self.textbox(slide, (cx, ry + 0.12, widths[ci] - 0.2, rh - 0.14), cell, size=15,
                             color="navy" if ci == 0 else "slate", bold=ci == 0, name=f"{name}/r{ri + 1}c{ci + 1}")
                cx += widths[ci]
        self.hline(slide, x, y + 0.6 + len(t["rows"]) * rh, w, name=f"{name}/rule_end")

    def evidence(self, slide, box, e, name):
        x, y, w, h = box
        kind = e.get("kind")
        if kind == "big_number":
            self.rect(slide, box, "tint", name=f"{name}/panel")
            value = e.get("value") or e.get("text_live", "")
            if h < 3:   # short band: figure left, label right
                self.textbox(slide, (x + 0.4, y, w * 0.45, h), value, font="head", size=60, color="navy",
                             anchor="middle", name=f"{name}/value")
                self.textbox(slide, (x + w * 0.5, y, w * 0.45, h), e.get("label", ""), size=16, line=1.3,
                             anchor="middle", name=f"{name}/label")
            else:
                self.textbox(slide, (x + 0.4, y + 0.45, w - 0.8, 1.5), value, font="head", size=72, color="navy",
                             name=f"{name}/value")
                self.textbox(slide, (x + 0.4, y + 2.0, w - 0.8, h - 2.3), e.get("label", ""), size=16, line=1.3,
                             name=f"{name}/label")
        elif kind == "chart":
            c = e["chart"]
            self.textbox(slide, (x, y, w, 0.32), c.get("title", ""), size=12, name=f"{name}/chart_title")
            inner = (x, y + 0.45, w, h - 0.45)
            if c.get("type") == "scatter":
                self.scatter(slide, inner, c, f"{name}/chart")
            elif c.get("type") == "line":
                self.line_chart(slide, inner, c, f"{name}/chart")
            else:
                self.chart(slide, inner, c, f"{name}/chart")
        elif kind == "diagram":
            if e["diagram"].get("nodes"):
                self.mechanism(slide, box, e["diagram"], f"{name}/mechanism")
            else:
                self.diagram(slide, box, e["diagram"], f"{name}/diagram")
        elif kind == "table" and e.get("table"):
            self.table(slide, box, e["table"], f"{name}/table")
        elif kind == "comparison":
            c = e["comparison"]
            half = (h - 0.25) / 2
            for i, side in enumerate(("left", "right")):
                by = y + i * (half + 0.25)
                self.rect(slide, (x, by, w, half), "tint" if side == "right" else None,
                          line=None if side == "right" else "rule", name=f"{name}/{side}/panel")
                self.textbox(slide, (x + 0.3, by + 0.22, w - 0.6, 0.3), c[side]["label"], size=13, bold=True,
                             color="navy" if side == "right" else "slate", name=f"{name}/{side}/label")
                self.textbox(slide, (x + 0.3, by + 0.62, w - 0.6, half - 0.8), c[side]["text"], font="head", size=18,
                             color="navy" if side == "right" else "slate", name=f"{name}/{side}/text")

    # ---------- main ----------
    def render(self, spec, variant, out_path, run=None, offset=0):
        prs = Presentation(self.template_path)
        ids = prs.slides._sldIdLst
        for sldId in list(ids):
            prs.part.drop_rel(sldId.rId)
            ids.remove(sldId)
        layouts = {l.name: l for l in prs.slide_layouts}
        sections = {s["id"]: (i + 1, s) for i, s in enumerate(spec.get("sections", []))}
        read_map = self.man["variant_policy"]["read_map"]

        plan = [dict(s) for s in spec["slides"]]
        positions = {s["id"]: i + 1 for i, s in enumerate(plan)}
        for a in spec.get("appendix", []):
            parent = positions.get(a.get("parent_claim"))
            label = f"Appendix {a['id']} | " + (f"Supports slide {parent}" if parent else
                                                 {"risk": "Open question"}.get(a.get("kind"), "Supporting material"))
            plan.append({"id": a["id"], "layout": "appendix", "headline": a["title"], "table": a.get("table"),
                         "label": label, "body": {"live": [], "read": "" if a.get("table") else a.get("text", "")},
                         "evidence": [{"role": "primary", "placement": "slide",
                                       "source": (a.get("table") or {}).get("source", "") or a.get("source", "")}]})

        state, report = {}, []
        for s in plan:
            sec_i, sec = sections.get(s.get("section"), (0, None))
            ctx = {"section": sec, "section_index": sec_i, "deck": spec.get("deck", {})}
            type_ = s["layout"] if variant == "live" else read_map.get(s["layout"], s["layout"])
            lid, why = self.choose(type_, s, ctx, variant, state, offset)
            state["_prev"] = lid
            lay = self.layouts[lid]
            slide = prs.slides.add_slide(layouts[lid])
            by_idx = {ph.placeholder_format.idx: ph for ph in slide.placeholders}
            names = {ph.name: ph.placeholder_format.idx for ph in layouts[lid].placeholders}
            overflow = []
            for p in lay["placeholders"]:
                shape = by_idx.get(names.get(p["name"]))
                if shape is None:
                    continue
                if p["role"] == "region":
                    shape._element.getparent().remove(shape._element)
                    self.draw_region(slide, p["region"], p["box"], s, s["id"], variant)
                    continue
                paras = self.resolve(p["maps_to"], s, ctx, variant)
                if not paras:
                    shape._element.getparent().remove(shape._element)
                    continue
                self.write_text(shape, paras, p)
                shape.name = f"{s['id']}/{p['name']}"
                cap = p["capacity"]
                need = sum(max(1, math.ceil(len(t) / cap["chars_per_line"])) for t in paras)
                if need > cap["lines"]:
                    overflow.append(f"{p['name']} ~{need} lines > {cap['lines']}")
            for lph in layouts[lid].placeholders:
                if lph.placeholder_format.type == PP_PLACEHOLDER.SLIDE_NUMBER:
                    el = copy.deepcopy(lph._element)
                    slide.shapes._spTree.append(el)
                    el.nvSpPr.cNvPr.set("name", f"{s['id']}/slide_number")
                    el.nvSpPr.cNvPr.set("id", str(900 + len(slide.shapes)))
            if s.get("notes"):
                slide.notes_slide.notes_text_frame.text = s["notes"]
            report.append({"slide": s["id"], "layout": lid, "why": why, "overflow": overflow})
            if run is not None:
                no_fit = why.startswith("NO FIT")
                decisions.log(run, stage="renderer", variant=variant, source="TEMPLATE",
                              action="layout_overflow" if no_fit else "choose_layout",
                              target={"type": "slide", "id": s["id"]}, result=lid, rationale=why,
                              kb_rules=lay["rules"], review_required=no_fit)
        prs.save(out_path)
        return report
