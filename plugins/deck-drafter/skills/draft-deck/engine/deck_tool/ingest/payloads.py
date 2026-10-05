"""
Deterministic checks on the structured payloads extraction proposes (chart, comparison,
diagram, table). Two things are checked, both without a model:

1. Shape: the payload matches the contract the renderers read (the same keys
   pptx_render.py and gslides/render.py index into), so a malformed payload is caught
   at ingestion instead of crashing a renderer three stages later.
2. Numbers: every number in the payload appears somewhere in the source document's
   own text. A structured payload is the one place a model could quietly introduce a
   figure the source never stated -- a chart series is a list of bare numbers with no
   sentence around them for a reviewer to read. This check catches that mechanically.
   Text in a payload (labels, step names) is not checked here; it is visible prose a
   reviewer can read, unlike a bare number in a series.

A payload that fails either check is removed and the item is kept as plain text, with
the reason logged by the caller -- the underlying fact is never dropped.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

from .readers import MARKER_RE

STRUCTURED_KINDS = ("chart", "comparison", "diagram", "table")

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _norm(token: str) -> str | None:
    try:
        d = Decimal(token.replace(",", ""))
    except InvalidOperation:
        return None
    return format(d.normalize(), "f")


def source_numbers(source_text: str) -> set[str]:
    """Every number in the document's own text, with reader line markers stripped so a
    marker like "L46" can never vouch for a figure of 46."""
    nums = set()
    for line in source_text.splitlines():
        m = MARKER_RE.match(line)
        body = m.group(2) if m else line
        nums.update(n for n in (_norm(t) for t in _NUM_RE.findall(body)) if n is not None)
    return nums


def _payload_numbers(value, skip_keys=("highlight", "id", "from", "to")) -> list[str]:
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k not in skip_keys:
                out += _payload_numbers(v, skip_keys)
    elif isinstance(value, list):
        for v in value:
            out += _payload_numbers(v, skip_keys)
    elif isinstance(value, bool):
        pass
    elif isinstance(value, (int, float)):
        n = _norm(repr(value))
        if n is not None:
            out.append(n)
    elif isinstance(value, str):
        out += [n for n in (_norm(t) for t in _NUM_RE.findall(value)) if n is not None]
    return out


def _is_str_list(x, min_len=1) -> bool:
    return isinstance(x, list) and len(x) >= min_len and all(isinstance(s, str) and s.strip() for s in x)


def _shape_problems(kind: str, p) -> list[str]:
    if not isinstance(p, dict):
        return [f"{kind} payload missing"]
    if kind == "chart" and p.get("type") == "scatter":
        series = p.get("series")
        if not isinstance(series, list) or not series:
            return ["scatter needs at least one series"]
        for se in series:
            pts = se.get("points") if isinstance(se, dict) else None
            if not isinstance(pts, list) or len(pts) < 3:
                return ["each scatter series needs at least 3 points"]
            if any(not (isinstance(pt, list) and len(pt) == 2 and
                        all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in pt)) for pt in pts):
                return ["scatter points must be [x, y] number pairs"]
        return []
    if kind == "diagram" and p.get("type") == "mechanism":
        nodes, edges = p.get("nodes"), p.get("edges")
        if not isinstance(nodes, list) or len(nodes) < 2:
            return ["mechanism needs at least 2 nodes"]
        ids = [n.get("id") for n in nodes if isinstance(n, dict)]
        if len(ids) != len(nodes) or len(set(ids)) != len(ids) or not all(
                isinstance(n.get("label"), str) and n["label"].strip() for n in nodes):
            return ["mechanism nodes need unique ids and labels"]
        if not isinstance(edges, list) or not edges:
            return ["mechanism needs at least one edge"]
        for e in edges:
            if not isinstance(e, dict) or e.get("from") not in ids or e.get("to") not in ids:
                return ["mechanism edges must connect known node ids"]
            if e.get("kind", "causes") not in ("causes", "inhibits", "feedback"):
                return [f"unknown mechanism edge kind {e.get('kind')!r}"]
        return []
    if kind == "chart":
        cats, series = p.get("categories"), p.get("series")
        if not _is_str_list(cats, 2):
            return ["chart needs at least 2 string categories"]
        if not isinstance(series, list) or not series:
            return ["chart needs at least one series"]
        for s in series:
            if not isinstance(s, dict) or not isinstance(s.get("name"), str):
                return ["each chart series needs a name"]
            vals = s.get("values")
            if not isinstance(vals, list) or len(vals) != len(cats):
                return [f"series {s.get('name')!r} has {len(vals) if isinstance(vals, list) else 0} "
                        f"values for {len(cats)} categories"]
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
                return [f"series {s.get('name')!r} has non-numeric values"]
        return []
    if kind == "comparison":
        for side in ("left", "right"):
            s = p.get(side)
            if not isinstance(s, dict) or not (s.get("label") or "").strip() or not (s.get("text") or "").strip():
                return [f"comparison needs {side}.label and {side}.text"]
        return []
    if kind == "diagram":
        if not _is_str_list(p.get("steps"), 2):
            return ["diagram needs at least 2 string steps"]
        hi = p.get("highlight")
        if hi is not None and not (isinstance(hi, int) and 0 <= hi < len(p["steps"])):
            return ["diagram highlight must be a step index"]
        return []
    if kind == "table":
        cols, rows = p.get("columns"), p.get("rows")
        if not _is_str_list(cols, 2):
            return ["table needs at least 2 columns"]
        if not isinstance(rows, list) or not rows:
            return ["table needs at least one row"]
        if any(not isinstance(r, list) or len(r) != len(cols) for r in rows):
            return ["every table row must have one cell per column"]
        return []
    return []


def check_payload(item: dict, source_text: str, known_numbers: set[str] | None = None) -> dict:
    """Returns {"shape": [...], "unsupported_numbers": [...]} -- both empty means the
    payload is safe to keep."""
    kind = item.get("kind")
    if kind not in STRUCTURED_KINDS:
        return {"shape": [], "unsupported_numbers": []}
    payload = item.get(kind)
    shape = _shape_problems(kind, payload)
    if shape:
        return {"shape": shape, "unsupported_numbers": []}
    known = known_numbers if known_numbers is not None else source_numbers(source_text)
    missing = sorted({n for n in _payload_numbers(payload) if n not in known})
    return {"shape": [], "unsupported_numbers": missing}
