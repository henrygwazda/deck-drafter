"""
Pure layout-manifest logic shared by every renderer backend: which layout variant a
slide gets (cycling, repeat-avoidance, capacity fit), and what text a manifest
placeholder's maps_to path resolves to for a given slide. No rendering-target
dependency (no python-pptx, no Google API) — this is the same decision logic the
PowerPoint renderer and the native Google Slides renderer both build on, so the two
never silently choose different layouts for the same slide.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

EYEBROW_FALLBACK = {"exec_summary": "Summary", "positioning": "Positioning", "alternatives_risks": "Open questions"}


def split_halves(text):
    sents = re.split(r"(?<=[.!?])\s+", text.strip())
    total = sum(len(x) for x in sents)
    acc, cut = 0, len(sents)
    for i, x in enumerate(sents):
        acc += len(x)
        if acc >= total / 2:
            cut = i + 1
            break
    return " ".join(sents[:cut]), " ".join(sents[cut:])


class LayoutEngine:
    def __init__(self, manifest_path):
        self.man = json.loads(Path(manifest_path).read_text())
        self.layouts = {l["id"]: l for l in self.man["layouts"]}
        self.by_type = {}
        for l in self.man["layouts"]:
            self.by_type.setdefault(l["type"], []).append(l["id"])
        t = self.man["tokens"]
        self.colors = {k: v["hex"] for k, v in t["colors"].items()}
        self.colors.update({"white": "FFFFFF", "tint": t["neutrals"]["tint"], "rule": t["neutrals"]["rule"]})
        self.fonts = t["fonts"]

    # ---------- helpers ----------
    def rgb(self, name):
        return self.colors.get(name, name)

    def font(self, key):
        return self.fonts.get(key, key)

    # ---------- spec resolution ----------
    @staticmethod
    def primary(slide):
        ev = [e for e in slide.get("evidence", []) if e.get("role") == "primary" and e.get("placement", "slide") == "slide"]
        return ev[0] if ev else {}

    @staticmethod
    def quote(slide):
        """The first supporting evidence item placed on the slide itself: the qualitative
        half of a mixed-evidence slide."""
        ev = [e for e in slide.get("evidence", []) if e.get("role") == "supporting" and e.get("placement") == "slide"]
        return ev[0] if ev else {}

    @staticmethod
    def _evidence_field(e: dict, key: str):
        """value and label fall back to the extracted text when an item has no explicit
        figure fields, so a big-number slide built from ingested evidence never renders
        with its figure missing. A full sentence is too long for a label, so the label
        fallback only applies to short text."""
        v = e.get(key)
        if v:
            return v
        if key == "value":
            return e.get("text_live", "")
        if key == "label":
            t = e.get("text_read", "")
            return t if len(t) <= 70 else ""
        return v

    def resolve(self, path, slide, ctx, variant):
        """Return a list of paragraphs (strings) for a maps_to path, or [] when absent."""
        body = slide.get("body", {})
        live, read = body.get("live", []) or [], body.get("read", "") or ""
        if path in ("slide.headline", "appendix.title"):
            return [slide.get("headline", "")] if slide.get("headline") else []
        if path == "slide.subhead":
            return [slide["subhead"]] if slide.get("subhead") else []
        if path.startswith("section."):
            sec = ctx["section"]
            if not sec:
                if path == "section.title":
                    label = slide.get("eyebrow") or EYEBROW_FALLBACK.get(slide.get("layout"))
                    return [label] if label else []
                return []
            key = path.split(".", 1)[1]
            if key == "number":
                return [f"{ctx['section_index']:02d}"]
            return [sec[key]] if sec.get(key) else []
        if path.startswith("deck."):
            key = path.split(".", 1)[1]
            return [ctx["deck"][key]] if ctx["deck"].get(key) else []
        if path == "appendix.label":
            return [slide.get("label", "")]
        if path == "body.live":
            return [x for x in live if x]
        m = re.match(r"body\.live\[(\d+)\](\.index)?$", path)
        if m:
            i = int(m.group(1))
            if i >= len(live):
                return []
            return [f"{i + 1:02d}"] if m.group(2) else [live[i]]
        if path == "body.read":
            return [read] if read else []
        m = re.match(r"body\.read#(\d)$", path)
        if m:
            halves = split_halves(read)
            return [halves[int(m.group(1)) - 1]] if read else []
        if path.startswith(("evidence.primary", "evidence.quote")):
            e = self.primary(slide) if path.startswith("evidence.primary") else self.quote(slide)
            keys = path.split(".")[2:]
            if len(keys) == 1 and keys[0] in ("value", "label") and e:
                v = self._evidence_field(e, keys[0])
            else:
                v = e
                for key in keys:
                    v = v.get(key, {}) if isinstance(v, dict) else {}
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                v = f"{v:g}"
            if isinstance(v, str) and v:
                return [v]
            return []
        return []

    # ---------- variant choice ----------
    def region_fit(self, p, slide) -> tuple[bool, str]:
        """Whether a region can draw the slide's primary evidence: kind allowed, and
        rows, categories, series, points, and nodes within the region's capacity."""
        cap = p.get("capacity") or {}
        e = self.primary(slide)
        if not cap or not e:
            return True, ""
        kind = e.get("kind")
        if cap.get("kinds") and kind not in cap["kinds"]:
            return False, f"{p['name']} region does not draw {kind} evidence"
        if p.get("region") == "evidence" and self.quote(slide) and p["box"][3] < 3:
            return False, f"{p['name']} region has no room for the slide's quote"
        chart, table, diagram = e.get("chart") or {}, e.get("table") or {}, e.get("diagram") or {}
        checks = [
            ("max_rows", len(table.get("rows", [])), "rows"),
            ("max_columns", len(table.get("columns", [])), "columns"),
            ("max_categories", len(chart.get("categories", [])), "categories"),
            ("max_series", len(chart.get("series", [])), "series"),
            ("max_points", sum(len(se.get("points", [])) for se in chart.get("series", [])), "points"),
            ("max_nodes", len(diagram.get("nodes", [])), "nodes"),
        ]
        for key, n, what in checks:
            if key in cap and n > cap[key]:
                return False, f"{p['name']} region holds {cap[key]} {what}, evidence has {n}"
        return True, ""

    def fits(self, layout_id, slide, ctx, variant):
        slot = self.layouts[layout_id].get("subhead_slot")
        if slot is not None and slot != bool((slide.get("subhead") or "").strip()):
            return False, ("has a subhead slot and the slide has no subhead" if slot
                           else "has no subhead slot for the slide's subhead")
        for p in self.layouts[layout_id]["placeholders"]:
            if p["role"] == "region":
                ok, why = self.region_fit(p, slide)
                if not ok:
                    return False, why
                continue
            if p["role"] != "text":
                continue
            paras = self.resolve(p["maps_to"], slide, ctx, variant)
            if not paras:
                continue
            cap = p["capacity"]
            needed = sum(max(1, math.ceil(len(t) / cap["chars_per_line"])) for t in paras)
            if needed > cap["lines"]:
                return False, f"{p['name']} needs {needed} lines, holds {cap['lines']}"
        return True, ""

    def choose(self, type_, slide, ctx, variant, state, offset):
        ids = self.by_type.get(type_)
        if not ids:
            raise KeyError(f"no layout for type {type_}")
        if slide.get("variant") and f"{type_}.{slide['variant']}" in ids:
            return f"{type_}.{slide['variant']}", "pinned by spec"
        n = len(ids)
        start = (state.get(type_, 0) + offset) % n
        order = [ids[(start + k) % n] for k in range(n)]
        notes = []
        for allow_repeat in (False, True):
            for lid in order:
                if lid == state.get("_prev") and n > 1 and not allow_repeat:
                    notes.append(f"{lid} skipped (same as previous slide)")
                    continue
                ok, why = self.fits(lid, slide, ctx, variant)
                if ok:
                    state[type_] = state.get(type_, 0) + 1
                    tail = "repeated previous layout because no other variant fits" if allow_repeat else "next in cycle"
                    return lid, "; ".join(notes + [f"{lid} {tail}"])
                if not allow_repeat:
                    notes.append(f"{lid} skipped ({why})")
        state[type_] = state.get(type_, 0) + 1
        # No silent overflow (brief section 10): the slide still renders so the deck can
        # be reviewed, but the reason is prefixed NO FIT so the renderer logs it for review
        # and lint's layout_fit check reports it.
        return order[0], "NO FIT: " + "; ".join(notes + [f"no variant holds this content, used {order[0]}"])
