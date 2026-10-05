"""
Milestone 6: the layered HTML version of a deck, rendered from the same v0.2 spec as
the PowerPoint and Slides outputs.

One self-contained file: inline CSS and JavaScript, inline SVG charts drawn from the
evidence payloads, no fonts, scripts, images, or requests from anywhere else, and no
tracking. Two modes over the same slides:

- Presenting: one slide at a time, keyboard and button navigation, live body text.
- Reading: every slide in sequence with its full read prose.

Every slide can expand its evidence: each item the slide cites (shown, quoted, or kept
in the notes) with its source document, locator, verification status, and any
ingestion flag that involves it, all looked up in the spec's canonical evidence and
source registries. Appendix entries appear in context on the slide they support, and
again in a closing appendix section. Nothing is written that is not in the spec: the
only fixed text is interface labels.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from .layout_engine import LayoutEngine

UI_LABELS = ("Presenting", "Reading", "Previous", "Next", "Evidence and sources", "Supporting material",
             "Appendix", "Unresolved source conflicts", "Source", "Verification", "Flag", "On slide",
             "Quoted on slide", "In speaker notes", "Shown on slide", "Concern", "Response", "Supports slide",
             "Open question", "Slide", "of", "Keyboard: arrow keys move, R switches mode.", "Feedback")


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""), quote=True)


# ---------- inline SVG ----------

def _num(v) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) else esc(v)


def svg_bars(c: dict, w=640, h=320) -> str:
    cats, series = c["categories"], c["series"]
    vals = series[0]["values"]
    hi = set(c.get("highlight", [])) or set(range(len(vals)))
    unit = "%" if c.get("unit") == "%" else ""
    vmax = max([v for v in vals if isinstance(v, (int, float))] + [1])
    horizontal = c.get("orientation") == "horizontal" or len(cats) > 6
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(c.get("title", "Bar chart"))}">']
    if horizontal:
        bh = (h - 20) / len(cats)
        for i, (cat, v) in enumerate(zip(cats, vals)):
            y = 10 + i * bh
            bw = (w - 260) * (v / vmax if vmax else 0)
            col = "var(--navy)" if i in hi else "var(--mist)"
            out.append(f'<text x="170" y="{y + bh * 0.6:.1f}" text-anchor="end" class="axis">{esc(cat)}</text>')
            out.append(f'<rect x="180" y="{y + bh * 0.15:.1f}" width="{bw:.1f}" height="{bh * 0.7:.1f}" fill="{col}"/>')
            out.append(f'<text x="{186 + bw:.1f}" y="{y + bh * 0.6:.1f}" class="lab">{_num(v)}{unit}</text>')
    else:
        bw = (w - 40) / len(cats)
        for i, (cat, v) in enumerate(zip(cats, vals)):
            x = 20 + i * bw
            bhh = (h - 70) * (v / vmax if vmax else 0)
            col = "var(--navy)" if i in hi else "var(--mist)"
            out.append(f'<rect x="{x + bw * 0.15:.1f}" y="{h - 40 - bhh:.1f}" width="{bw * 0.7:.1f}" height="{bhh:.1f}" fill="{col}"/>')
            out.append(f'<text x="{x + bw / 2:.1f}" y="{h - 46 - bhh:.1f}" text-anchor="middle" class="lab">{_num(v)}{unit}</text>')
            out.append(f'<text x="{x + bw / 2:.1f}" y="{h - 18}" text-anchor="middle" class="axis">{esc(cat)}</text>')
        out.append(f'<line x1="20" y1="{h - 40}" x2="{w - 20}" y2="{h - 40}" class="rule"/>')
    out.append("</svg>")
    return "".join(out)


def svg_line(c: dict, w=640, h=320) -> str:
    cats, series = c["categories"], c["series"]
    allv = [v for s in series for v in s["values"] if isinstance(v, (int, float))]
    lo, hi_ = min(allv + [0]), max(allv + [1])
    unit = "%" if c.get("unit") == "%" else ""
    px = lambda i: 50 + i * (w - 90) / max(1, len(cats) - 1)
    py = lambda v: h - 40 - (h - 80) * ((v - lo) / (hi_ - lo) if hi_ > lo else 0.5)
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(c.get("title", "Line chart"))}">',
           f'<line x1="40" y1="{h - 40}" x2="{w - 30}" y2="{h - 40}" class="rule"/>']
    for i, cat in enumerate(cats):
        out.append(f'<text x="{px(i):.1f}" y="{h - 18}" text-anchor="middle" class="axis">{esc(cat)}</text>')
    for si, s in enumerate(series):
        col = "var(--navy)" if si == 0 else "var(--mist)"
        pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(s["values"]))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="3"/>')
        for i, v in enumerate(s["values"]):
            out.append(f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="5" fill="{col}"/>')
            if i in (0, len(s["values"]) - 1):
                out.append(f'<text x="{px(i):.1f}" y="{py(v) - 12:.1f}" text-anchor="middle" class="lab">{_num(v)}{unit}</text>')
    out.append("</svg>")
    return "".join(out)


def svg_scatter(c: dict, w=640, h=320) -> str:
    pts = [(x, y, si) for si, s in enumerate(c["series"]) for x, y in s["points"]]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    xlo, xhi, ylo, yhi = min(xs), max(xs), min(0, min(ys)), max(ys)
    pad = (xhi - xlo) * 0.1 or 1
    xlo, xhi = xlo - pad, xhi + pad
    px = lambda x: 60 + (w - 90) * (x - xlo) / (xhi - xlo)
    py = lambda y: h - 50 - (h - 80) * ((y - ylo) / (yhi - ylo) if yhi > ylo else 0.5)
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(c.get("title", "Scatterplot"))}">',
           f'<line x1="55" y1="{h - 50}" x2="{w - 25}" y2="{h - 50}" class="rule"/>',
           f'<line x1="55" y1="20" x2="55" y2="{h - 50}" class="rule"/>',
           f'<text x="{w / 2:.0f}" y="{h - 12}" text-anchor="middle" class="axis">{esc(c.get("x_label", ""))}</text>',
           f'<text x="16" y="{h / 2:.0f}" text-anchor="middle" class="axis" transform="rotate(-90 16 {h / 2:.0f})">'
           f'{esc(c.get("y_label", ""))}</text>']
    for x, y in ((xlo + pad, 0), (xhi - pad, 0)):
        out.append(f'<text x="{px(x):.1f}" y="{h - 32}" text-anchor="middle" class="axis">{_num(x)}</text>')
    for x, y, si in pts:
        shape = (f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="6" fill="var(--navy)"/>' if si == 0 else
                 f'<rect x="{px(x) - 5:.1f}" y="{py(y) - 5:.1f}" width="10" height="10" fill="var(--mist)"/>')
        out.append(shape)
        out.append(f'<text x="{px(x) + 9:.1f}" y="{py(y) - 8:.1f}" class="lab small">{_num(y)}</text>')
    out.append("</svg>")
    return "".join(out)


def svg_flow(d: dict, w=640, h=150) -> str:
    steps, hi = d["steps"], d.get("highlight")
    n = len(steps)
    gap = 26
    bw = (w - gap * (n - 1)) / n
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Process">']
    for i, s in enumerate(steps):
        x = i * (bw + gap)
        on = i == hi
        out.append(f'<rect x="{x:.1f}" y="20" width="{bw:.1f}" height="{h - 40}" rx="6" fill="{"var(--navy)" if on else "var(--tint)"}"/>')
        out.append(f'<foreignObject x="{x + 8:.1f}" y="26" width="{bw - 16:.1f}" height="{h - 52}">'
                   f'<div class="node{" on" if on else ""}">{i + 1:02d}<br>{esc(s)}</div></foreignObject>')
        if i < n - 1:
            out.append(f'<path d="M{x + bw + 4:.1f},{h / 2:.1f} l{gap - 10},0" class="arrow"/>')
    out.append("</svg>")
    return "".join(out)


def svg_mechanism(d: dict, w=640, h=300) -> str:
    from .pptx_render import Renderer
    layer, forward, back = Renderer._mechanism_layout(d)
    ncols = max(layer.values()) + 1
    cols = {}
    for n in d["nodes"]:
        cols.setdefault(layer[n["id"]], []).append(n)
    gap = 36
    bw = (w - gap * (ncols - 1)) / ncols
    lane = 40 if back else 0
    pos = {}
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Mechanism">']
    notes = {}
    for e in forward:
        if e.get("label"):
            notes.setdefault(e["to"], []).append(e["label"])
    for ci in range(ncols):
        nodes = cols.get(ci, [])
        bh = min(70, (h - lane - 20 * (len(nodes) - 1)) / max(1, len(nodes)))
        y0 = (h - lane - (len(nodes) * bh + 20 * (len(nodes) - 1))) / 2
        for ni, n in enumerate(nodes):
            x, y = ci * (bw + gap), y0 + ni * (bh + 20)
            on = n["id"] == d.get("highlight")
            pos[n["id"]] = (x, y, bw, bh)
            note = "".join(f'<br><i>({esc(t)})</i>' for t in notes.get(n["id"], []))
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="8" '
                       f'fill="{"var(--navy)" if on else "var(--tint)"}" stroke="var(--rule)"/>')
            out.append(f'<foreignObject x="{x + 6:.1f}" y="{y + 4:.1f}" width="{bw - 12:.1f}" height="{bh - 8:.1f}">'
                       f'<div class="node{" on" if on else ""}">{esc(n["label"])}{note}</div></foreignObject>')
    for e in forward:
        fx, fy, fw, fh = pos[e["from"]]
        tx, ty, tw, th = pos[e["to"]]
        out.append(f'<line x1="{fx + fw:.1f}" y1="{fy + fh / 2:.1f}" x2="{tx - 4:.1f}" y2="{ty + th / 2:.1f}" '
                   f'class="arrow{" inhibit" if e.get("kind") == "inhibits" else ""}"/>')
    for j, e in enumerate(back):
        fx, fy, fw, fh = pos[e["from"]]
        tx, ty, tw, th = pos[e["to"]]
        ly = h - lane / 2 + j * 6
        out.append(f'<path d="M{fx + fw / 2:.1f},{fy + fh:.1f} V{ly:.1f} H{tx + tw / 2:.1f} V{ty + th + 4:.1f}" class="feedback"/>')
        out.append(f'<text x="{(fx + tx + (fw + tw) / 2) / 2:.1f}" y="{ly - 4:.1f}" text-anchor="middle" class="axis small">'
                   f'feedback: {esc(e.get("label") or "")}</text>')
    out.append("</svg>")
    return "".join(out)


def table_html(t: dict) -> str:
    head = "".join(f"<th>{esc(c)}</th>" for c in t["columns"])
    rows = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in t["rows"])
    cap = f"<caption>{esc(t.get('title', ''))}</caption>" if t.get("title") else ""
    return f'<table class="data">{cap}<thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>'


def evidence_visual(e: dict) -> str:
    kind = e.get("kind")
    c, d = e.get("chart") or {}, e.get("diagram") or {}
    if kind == "chart" and c.get("type") == "scatter" and c.get("series"):
        body = svg_scatter(c)
    elif kind == "chart" and c.get("categories"):
        body = svg_line(c) if c.get("type") == "line" else svg_bars(c)
    elif kind == "diagram" and d.get("nodes"):
        body = svg_mechanism(d)
    elif kind == "diagram" and d.get("steps"):
        body = svg_flow(d)
    elif kind == "comparison" and e.get("comparison"):
        cm = e["comparison"]
        body = ('<div class="compare">' + "".join(
            f'<div class="side {side}"><b>{esc(cm[side]["label"])}</b><p>{esc(cm[side]["text"])}</p></div>'
            for side in ("left", "right")) + "</div>")
    elif kind == "table" and e.get("table"):
        body = table_html(e["table"])
    else:
        value = LayoutEngine._evidence_field(e, "value") if e else ""
        label = LayoutEngine._evidence_field(e, "label") if e else ""
        if not value:
            return ""
        body = f'<div class="big"><span class="fig">{esc(value)}</span><span class="figlab">{esc(label)}</span></div>'
    title = c.get("title") if kind == "chart" else ""
    return (f'<figure class="evidence">{f"<figcaption>{esc(title)}</figcaption>" if title else ""}{body}</figure>')


# ---------- page ----------

CSS = """
:root{--navy:#16233F;--ochre:#D98E04;--slate:#4A5568;--mist:#A0AEC0;--tint:#F2F4F7;--rule:#D9DEE5;--bg:#E9ECF1;
--head:Cambria,Georgia,'Times New Roman',serif;--body:Arial,'Helvetica Neue',Helvetica,sans-serif}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--slate);font-family:var(--body);font-size:16px}
header.bar{position:sticky;top:env(safe-area-inset-top,0px);z-index:5;display:flex;gap:12px;align-items:center;flex-wrap:wrap;
padding:10px 16px;background:var(--navy);color:#fff}
header.bar h1{font:600 15px var(--body);margin:0;flex:1 1 12rem;min-width:0;overflow-wrap:anywhere}
header.bar button{font:14px var(--body);background:transparent;color:#fff;border:1px solid var(--mist);padding:6px 12px;border-radius:4px;cursor:pointer}
header.bar button[aria-pressed=true]{background:#fff;color:var(--navy)}
main{max-width:1100px;margin:0 auto;padding:16px}
.slide{background:#fff;margin:0 0 24px;padding:clamp(18px,4vw,48px);position:relative;border-radius:4px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.mode-present .slide{display:none;aspect-ratio:16/9;overflow:auto;margin:0}
@media (max-width:700px){.mode-present .slide{aspect-ratio:auto;min-height:60vh}}
h2,.points,.prose,td,th{overflow-wrap:anywhere}
.mode-present .slide.current{display:block}
.slide.dark{background:var(--navy);color:var(--mist)}
.slide.dark h2{color:#fff}
.eyebrow{font:700 13px var(--body);color:var(--slate);letter-spacing:.02em;margin:0 0 10px}
.eyebrow::before{content:"";display:inline-block;width:9px;height:9px;background:var(--ochre);margin-right:8px}
h2{font:400 clamp(22px,3vw,32px)/1.15 var(--head);color:var(--navy);margin:0 0 20px}
.subhead{font-size:20px;color:var(--mist)}
.claimsub{font-size:17px;line-height:1.4;color:var(--slate);margin:-10px 0 20px;max-width:80ch}
.points{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px 28px;padding:0;list-style:none;font-size:18px;line-height:1.4}
.points li{border-top:2px solid var(--rule);padding-top:8px}
.prose{font-size:16px;line-height:1.55;max-width:70ch;white-space:pre-line}
.mode-present .prose{display:none}.mode-read .points.live{display:none}
.claimgrid{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:28px;align-items:start}
@media (max-width:700px){.claimgrid{grid-template-columns:1fr}}
figure.evidence{margin:0}figure.evidence svg{width:100%;height:auto;display:block}
figcaption{font-size:13px;margin-bottom:6px}
.axis{font:12px var(--body);fill:var(--slate)}.lab{font:700 13px var(--body);fill:var(--navy)}.small{font-size:11px}
.rule{stroke:var(--rule);stroke-width:1.5}
.arrow{stroke:var(--slate);stroke-width:2;fill:none;marker-end:url(#arrowhead)}
.feedback{stroke:var(--ochre);stroke-width:2;stroke-dasharray:6 4;fill:none;marker-end:url(#arrowhead)}
.node{font:700 13px/1.25 var(--body);color:var(--navy);text-align:center;height:100%;display:flex;flex-direction:column;justify-content:center}
.node.on{color:#fff}.node i{font-weight:400;font-size:11px}
.big{display:flex;gap:20px;align-items:center;background:var(--tint);padding:24px}
.fig{font:400 clamp(44px,7vw,80px) var(--head);color:var(--navy)}.figlab{font-size:16px}
.compare{display:grid;grid-template-columns:1fr 1fr;gap:16px}.compare .side{padding:16px;border:1px solid var(--rule)}
.compare .right{background:var(--tint);color:var(--navy);border-color:var(--tint)}
blockquote{margin:0;padding:16px 20px;border-left:3px solid var(--ochre);font:18px/1.4 var(--head);color:var(--navy)}
blockquote cite{display:block;font:12px var(--body);color:var(--slate);margin-top:8px;font-style:normal}
table.data{border-collapse:collapse;width:100%;font-size:15px}table.data caption{text-align:left;font-size:13px;padding-bottom:6px}
table.data th{text-align:left;border-bottom:2px solid var(--rule);padding:6px 8px}table.data td{border-bottom:1px solid var(--rule);padding:6px 8px}
.risks td:first-child{font-family:var(--head);color:var(--navy);width:45%}
details{margin-top:18px;border-top:1px solid var(--rule);padding-top:10px;font-size:14px}
summary{cursor:pointer;color:var(--navy);font-weight:700}
.ev{padding:8px 0;border-bottom:1px dashed var(--rule)}.ev .meta{font-size:12px;color:var(--slate)}
.badge{display:inline-block;font-size:11px;padding:1px 6px;border-radius:9px;background:var(--tint);margin-right:6px}
.badge.flag{background:#FBE9C8;color:#7A4E00}
.slideno{position:absolute;right:16px;bottom:10px;font-size:12px;color:var(--mist)}
nav.pager{display:none;justify-content:center;gap:12px;align-items:center;margin:12px 0}
.mode-present nav.pager{display:flex}
nav.pager button{font:14px var(--body);padding:6px 14px;border:1px solid var(--slate);background:#fff;border-radius:4px;cursor:pointer}
.hint{font-size:12px;color:var(--slate);text-align:center}
section.appendix h3,section.flags h3{font:400 22px var(--head);color:var(--navy)}
.mode-present section.appendix,.mode-present section.flags{display:none}
"""

JS = """
(function(){
  var slides=[].slice.call(document.querySelectorAll('article.slide'));
  var i=0, body=document.body;
  function show(n){i=Math.max(0,Math.min(slides.length-1,n));
    slides.forEach(function(s,k){s.classList.toggle('current',k===i);});
    var c=document.getElementById('counter'); if(c){c.textContent=(i+1)+' / '+slides.length;}
    if(body.classList.contains('mode-present')){history.replaceState(null,'','#'+(i+1));}}
  function mode(m){body.classList.toggle('mode-present',m==='present');body.classList.toggle('mode-read',m==='read');
    document.getElementById('btn-present').setAttribute('aria-pressed',m==='present');
    document.getElementById('btn-read').setAttribute('aria-pressed',m==='read');
    try{localStorage.setItem('deckmode',m);}catch(e){} show(i);}
  document.getElementById('btn-present').onclick=function(){mode('present');};
  document.getElementById('btn-read').onclick=function(){mode('read');};
  document.getElementById('prev').onclick=function(){show(i-1);};
  document.getElementById('next').onclick=function(){show(i+1);};
  document.addEventListener('keydown',function(e){
    if(e.target.tagName==='SUMMARY')return;
    if(e.key==='r'||e.key==='R'){mode(body.classList.contains('mode-present')?'read':'present');}
    if(!body.classList.contains('mode-present'))return;
    if(e.key==='ArrowRight'||e.key===' '||e.key==='PageDown'){e.preventDefault();show(i+1);}
    if(e.key==='ArrowLeft'||e.key==='PageUp'){e.preventDefault();show(i-1);}});
  var start=parseInt((location.hash||'').slice(1),10); if(start>0){i=start-1;}
  var saved=null; try{saved=localStorage.getItem('deckmode');}catch(e){}
  mode(saved||'present');
})();
"""


class HtmlRenderer:
    def __init__(self, spec: dict):
        if spec.get("spec_version") != "0.2":
            raise ValueError("the HTML renderer reads the canonical v0.2 spec (evidence and source registries)")
        self.spec = spec
        self.evidence = {e["id"]: e for e in spec["evidence"]}
        self.sources = {s["id"]: s for s in spec["sources"]}
        self.sections = {s["id"]: s for s in spec.get("sections", [])}
        self.positions = {s["id"]: i + 1 for i, s in enumerate(spec["slides"])}
        self.flags_by_ev = {}
        for f in spec.get("flags", []):
            for eid in f.get("evidence", []):
                self.flags_by_ev.setdefault(eid, []).append(f)
        self.appendix_by_parent = {}
        for a in spec.get("appendix", []):
            self.appendix_by_parent.setdefault(a.get("parent_claim"), []).append(a)

    def _source_line(self, e: dict) -> str:
        prov = e.get("provenance", {}) or {}
        src = self.sources.get(prov.get("source_id"), {})
        return ", ".join(x for x in (src.get("filename") or src.get("id", ""), prov.get("locator", "")) if x)

    def _ev_item(self, eid: str, where: str) -> str:
        e = self.evidence.get(eid)
        if not e:
            return ""
        prov = e.get("provenance", {}) or {}
        flags = "".join(f'<span class="badge flag" title="{esc(f.get("note", ""))}">Flag {esc(f.get("id", ""))}</span>'
                        for f in self.flags_by_ev.get(eid, []))
        return (f'<div class="ev" data-evidence="{esc(eid)}"><span class="badge">{esc(where)}</span>'
                f'<span class="badge">{esc(eid)}</span>{flags}<div>{esc(e.get("text_read") or e.get("text_live"))}</div>'
                f'<div class="meta">Source: {esc(self._source_line(e))} · Verification: '
                f'{esc(prov.get("verification", ""))}{(" · " + esc(prov.get("transform"))) if prov.get("transform") else ""}'
                f'</div></div>')

    def _evidence_panel(self, s: dict) -> str:
        refs = s.get("evidence", [])
        where = {("primary", "slide"): "Shown on slide", ("supporting", "slide"): "Quoted on slide"}
        items = "".join(self._ev_item(r["ref"], where.get((r.get("role"), r.get("placement")), "In speaker notes"))
                        for r in refs)
        appx = "".join(self._appendix_item(a) for a in self.appendix_by_parent.get(s["id"], []))
        parts = []
        if items:
            parts.append(f'<details><summary>Evidence and sources ({len(refs)})</summary>{items}</details>')
        if appx:
            parts.append(f'<details><summary>Supporting material ({len(self.appendix_by_parent[s["id"]])})</summary>{appx}</details>')
        return "".join(parts)

    def _appendix_item(self, a: dict) -> str:
        body = table_html(a["table"]) if a.get("table") else f'<p class="prose" style="display:block">{esc(a.get("text", ""))}</p>'
        refs = [a["evidence_ref"]] if a.get("evidence_ref") else a.get("evidence_refs", [])
        src = "".join(self._ev_item(r, "Source") for r in refs)
        return (f'<div class="ev" id="appx-{esc(a["id"])}"><span class="badge">{esc(a["id"])}</span>'
                f'<b>{esc(a["title"])}</b>{body}{src}</div>')

    def _slide(self, s: dict, n: int, total: int) -> str:
        layout = s["layout"]
        sec = self.sections.get(s.get("section"))
        dark = layout in ("title", "section_divider", "closing")
        eyebrow = (sec or {}).get("title") if layout not in ("section_divider", "title", "closing") else ""
        eyebrow = eyebrow or {"exec_summary": "Summary", "positioning": "Positioning",
                              "alternatives_risks": "Open questions"}.get(layout, "")
        head = f'<p class="eyebrow">{esc(eyebrow)}</p>' if eyebrow and not dark else ""
        live = [x for x in (s.get("body") or {}).get("live", []) if x]
        read = (s.get("body") or {}).get("read", "")
        inner = ""
        if layout == "title":
            inner = f'<h2>{esc(s.get("headline"))}</h2><p class="subhead">{esc(s.get("subhead", ""))}</p>'
        elif layout == "section_divider":
            inner = (f'<p class="eyebrow" style="color:var(--ochre)">{list(self.sections).index(s["section"]) + 1:02d}</p>'
                     f'<h2>{esc(s.get("headline"))}</h2><p class="subhead">{esc((sec or {}).get("eyebrow", ""))}</p>'
                     f'<p>{esc((sec or {}).get("summary", ""))}</p>')
        elif layout == "closing":
            inner = (f'<h2>{esc(s.get("headline"))}</h2>'
                     f'<p style="color:var(--ochre);font-weight:700;font-size:20px">{esc(live[0] if live else "")}</p>')
        elif layout == "alternatives_risks":
            rows = "".join(f'<tr><td>{esc(r.get("text"))}</td><td>{esc(r.get("mitigation"))}</td></tr>'
                           for r in s.get("risks", []))
            inner = (f'<h2>{esc(s.get("headline"))}</h2><table class="data risks points live"><thead><tr><th>Concern</th>'
                     f'<th>Response</th></tr></thead><tbody>{rows}</tbody></table><div class="prose">{esc(read)}</div>')
        else:
            refs = s.get("evidence", [])
            prim = next((self.evidence.get(r["ref"]) for r in refs if r.get("role") == "primary" and r.get("placement") == "slide"), None)
            quote = next((self.evidence.get(r["ref"]) for r in refs if r.get("role") == "supporting" and r.get("placement") == "slide"), None)
            visual = evidence_visual(prim) if prim else ""
            if quote:
                visual += (f'<blockquote>{esc(quote.get("text_read"))}<cite>{esc(self._source_line(quote))}</cite></blockquote>')
            pts = "".join(f"<li>{esc(x)}</li>" for x in live)
            text = (f'<ul class="points live">{pts}</ul>' if pts else "") + f'<div class="prose">{esc(read)}</div>'
            sub = f'<p class="claimsub">{esc(s["subhead"])}</p>' if s.get("subhead") else ""
            inner = f'<h2>{esc(s.get("headline"))}</h2>{sub}' + (
                f'<div class="claimgrid"><div>{visual}</div><div>{text}</div></div>' if visual else text)
        return (f'<article class="slide{" dark" if dark else ""}" id="{esc(s["id"])}" data-layout="{esc(layout)}">'
                f'{head}{inner}{self._evidence_panel(s)}<span class="slideno">{n} / {total}</span></article>')

    def render(self) -> str:
        spec = self.spec
        title = spec["slides"][0].get("headline") if spec["slides"] else spec["deck_id"]
        total = len(spec["slides"])
        slides = "".join(self._slide(s, i + 1, total) for i, s in enumerate(spec["slides"]))
        appendix = ""
        if spec.get("appendix"):
            appendix = ('<section class="appendix slide"><h3>Appendix</h3>' +
                        "".join(self._appendix_item(a) for a in spec["appendix"]) + "</section>")
        flags = ""
        if spec.get("flags"):
            flags = ('<section class="flags slide"><h3>Unresolved source conflicts</h3>' + "".join(
                f'<div class="ev"><span class="badge flag">{esc(f.get("id", ""))}</span>{esc(f.get("note", ""))}'
                f'<div class="meta">{esc(", ".join(f.get("evidence", [])))}</div></div>' for f in spec["flags"]) + "</section>")
        defs = ('<svg width="0" height="0" style="position:absolute"><defs><marker id="arrowhead" viewBox="0 0 10 10" '
                'refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                '<path d="M0,0 L10,5 L0,10 z" fill="#4A5568"/></marker></defs></svg>')
        return (f"<title>{esc(title)}</title><style>{CSS}</style>"
                f'<header class="bar"><h1>{esc(title)}</h1>'
                f'<button id="btn-present" aria-pressed="true">Presenting</button>'
                f'<button id="btn-read" aria-pressed="false">Reading</button></header>{defs}'
                f'<main><nav class="pager"><button id="prev">Previous</button><span id="counter"></span>'
                f'<button id="next">Next</button></nav>{slides}{appendix}{flags}'
                f'<p class="hint">Keyboard: arrow keys move, R switches mode.</p></main><script>{JS}</script>')

    def document(self) -> str:
        """A standalone file for opening locally (the artifact skeleton adds its own)."""
        return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
                f'</head><body>{self.render()}</body></html>')


def render_html(spec: dict, out_path) -> Path:
    out = Path(out_path)
    out.write_text(HtmlRenderer(spec).document())
    return out
