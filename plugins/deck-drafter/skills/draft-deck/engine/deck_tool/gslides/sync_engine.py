"""
Milestone 7: synchronisation from a native Google Slides deck back into the spec
(docs/GOOGLE_SLIDES_DESIGN.md section 7, docs/NARRATIVE_DESIGN.md section 4).

The loop: read the deck, diff it against the snapshot saved at creation or last sync,
turn each change into a change to the persistent v0.2 spec using the element meta the
renderer recorded, log every change as a HUMAN decision, check whether the edits now
diverge from the approved narrative, and save a new snapshot.

What is applied and what is flagged:
- Body, label, section, and risk-row text edits are applied.
- Headline edits are applied. Whether one changes the argument is judged per edit;
  only a material change is flagged for review, so minor wording does not trigger a
  narrative revision.
- An edited figure, chart value, table cell, or diagram label is never written over
  the sourced evidence. A new evidence item is created, derived from the original,
  marked unverified with the edit as its provenance, and the slide points to it. The
  original stays in the registry. Always flagged, since a sourced figure changed.
- Reordered slides are applied. A reorder that puts an argument before one the
  approved progression introduces first is flagged as a possible logic break.
- A deleted slide leaves the deck, but its claim and evidence stay in the registries
  and the slide is kept in retired_slides. Deleting a primary or supporting argument's
  slide is flagged as a deleted premise.
- Deleted evidence elements move the evidence to the notes, not out of the deck's
  record. New text boxes and new or duplicated slides are recorded and flagged; text
  that asserts a conclusion is checked for support.

Nothing here edits the approved narrative. Divergences are written to
narrative_divergence.json with the three options from NARRATIVE_DESIGN section 4:
accept the change and revise the narrative, revise the slide, or keep the difference as
an acknowledged editorial exception.
"""
from __future__ import annotations

import copy
import json
import re
import time

from .. import decisions
from ..common import call_json, fill, llm_mode, load_json, run_dir, save_json
from ..spec import validate
from .spike import diff

OPTIONS = ["accept the change and revise the approved narrative", "revise the slide",
           "keep the difference as an acknowledged editorial exception"]
_PAGE = re.compile(r"^dt_(SL\d+|A\d+)$")
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _slide_of_page(page: str) -> str | None:
    m = _PAGE.match(page)
    return m.group(1) if m else None


def _num(text: str):
    m = _NUM.search(text or "")
    if not m:
        return None
    v = float(m.group(0).replace(",", ""))
    return int(v) if v.is_integer() else v


class Sync:
    def __init__(self, spec: dict, snapshot: dict, narrative: dict | None, run):
        self.spec = copy.deepcopy(spec)
        self.snap, self.narrative, self.run = snapshot, narrative or {}, run
        self.meta = snapshot.get("meta", {})
        self.slides = {s["id"]: s for s in self.spec["slides"]}
        self.evidence = {e["id"]: e for e in self.spec["evidence"]}
        self.applied, self.flagged, self.divergence, self.judge_queue = [], [], [], []

    # ---------- bookkeeping ----------
    def log(self, action, target, result, review, rationale="", rules=()):
        decisions.log(self.run, stage="gslides_sync", source="HUMAN", action=action, target=target, result=result,
                      rationale=rationale, kb_rules=list(rules), review_required=review)
        (self.flagged if review else self.applied).append({"action": action, "target": target, "result": result})

    def diverge(self, kind, slide_id, detail, change):
        self.divergence.append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, "slide": slide_id,
                                "detail": detail, "change": change, "options": OPTIONS, "status": "open"})
        self.log("narrative_divergence", {"type": "slide", "id": slide_id}, f"{kind}: {detail}", True,
                 rationale="Options: " + "; ".join(OPTIONS), rules=["NAR-01", "NAR-09"])

    def _next_ev_id(self):
        n = max(int(e[2:]) for e in self.evidence) + 1
        return f"EV{n:02d}"

    def _arg_of(self, slide):
        cl = next((c for c in self.spec["claims"] if c["id"] == slide.get("claim")), None)
        if not cl:
            return None
        return next((a for a in self.narrative.get("arguments", []) if a["id"] == cl.get("argument_ref")), None)

    # ---------- text ----------
    def text_edit(self, ch, info):
        sid = info["slide"]
        maps = info.get("maps_to", "")
        new = ch["to"]
        if sid.startswith("A"):
            entry = next((a for a in self.spec.get("appendix", []) if a["id"] == sid), None)
            if entry and maps == "appendix.title":
                entry["title"] = new
                return self.log("appendix_text_edit", {"type": "appendix", "id": sid}, new, False)
            if entry and maps == "body.read":
                entry["text"] = new
                return self.log("appendix_text_edit", {"type": "appendix", "id": sid}, new, False)
            return self.log("unhandled_edit", {"type": "element", "id": ch["id"]}, new, True)
        s = self.slides.get(sid)
        if s is None:
            return
        if maps in ("slide.headline",):
            old = s.get("headline", "")
            s["headline"] = new
            self.judge_queue.append({"id": ch["id"], "slide": sid, "kind": "headline", "from": old, "to": new})
            return
        if maps == "slide.subhead":
            s["subhead"] = new
        elif maps == "body.live":
            s.setdefault("body", {})["live"] = [x for x in new.split("\n") if x.strip()]
        elif re.match(r"body\.live\[(\d+)\]$", maps):
            i = int(re.match(r"body\.live\[(\d+)\]$", maps).group(1))
            live = s.setdefault("body", {}).setdefault("live", [])
            while len(live) <= i:
                live.append("")
            live[i] = new
        elif maps == "body.read":
            s.setdefault("body", {})["read"] = new
        elif maps.startswith("body.read#"):
            other = "2" if maps.endswith("1") else "1"
            other_id = ch["id"].rsplit("_", 1)[0] + f"_prose_{other}"
            other_text = self.current.get(other_id, {}).get("text", "")
            parts = [new, other_text] if maps.endswith("1") else [other_text, new]
            s.setdefault("body", {})["read"] = " ".join(p for p in parts if p)
        elif maps.startswith("section."):
            sec = next((x for x in self.spec.get("sections", []) if x["id"] == s.get("section")), None)
            key = {"section.title": "title", "section.eyebrow": "eyebrow", "section.summary": "summary"}.get(maps)
            if sec is None or key is None:
                return self.log("unhandled_edit", {"type": "element", "id": ch["id"]}, new, True)
            sec[key] = new
            if s["layout"] == "section_divider" and key == "title":
                s["headline"] = new
        elif maps.startswith("deck."):
            self.spec.setdefault("deck", {})[maps.split(".", 1)[1]] = new
        elif maps.startswith("evidence.primary"):
            field = maps.split(".", 2)[2]
            return self.data_edit(ch, {"slide": sid, "evidence": self._primary_id(s), "field": field})
        elif maps.startswith("evidence.quote"):
            return self.log("quote_edit_rejected", {"type": "element", "id": ch["id"]},
                            "A quoted source item was edited on the slide. Quotes stay verbatim; the edit is not "
                            "applied to the evidence.", True)
        else:
            return self.log("unhandled_edit", {"type": "element", "id": ch["id"]}, new, True)
        self.log("text_edit", {"type": "slide", "id": sid}, f"{maps}: {new}", False, rationale=f"was: {ch['from']!r}")

    def _primary_id(self, s):
        return next((r["ref"] for r in s.get("evidence", []) if r.get("role") == "primary" and r.get("placement") == "slide"), None)

    # ---------- data ----------
    def data_edit(self, ch, info):
        sid, eid, field = info["slide"], info.get("evidence"), info.get("field")
        s = self.slides.get(sid)
        if not s or eid not in self.evidence:
            return self.log("unhandled_edit", {"type": "element", "id": ch["id"]}, ch["to"], True)
        new = copy.deepcopy(self.evidence[eid])
        new_id = self._next_ev_id()
        new["id"] = new_id
        new["derived_from"] = [eid]
        new["disposition"] = "primary"
        if field == "value":
            new["value"] = ch["to"]
            new["text_live"] = ch["to"]
        elif field == "label":
            new["label"] = ch["to"]
        elif field == "chart":
            v = _num(ch["to"])
            if v is None:
                return self.log("unhandled_edit", {"type": "element", "id": ch["id"]}, ch["to"], True)
            new["chart"]["series"][0]["values"][info["index"]] = v
        elif field == "chart.title":
            new["chart"]["title"] = ch["to"]
        elif field.startswith("comparison."):
            side = field.split(".")[1]
            new["comparison"][side]["text" if field.endswith("text") else "label"] = ch["to"]
        elif field == "table":
            new["table"]["rows"][info["row"]][info["col"]] = ch["to"]
        elif field == "diagram_step":
            new["diagram"]["steps"][info["index"]] = ch["to"].split("\n", 1)[-1]
        elif field == "mechanism_node":
            for n in new["diagram"]["nodes"]:
                if n["id"] == info["node"]:
                    n["label"] = ch["to"].split("\n")[0]
        else:
            return self.log("unhandled_edit", {"type": "element", "id": ch["id"]}, ch["to"], True)
        new["provenance"] = {"locator": f"Google Slides edit to {ch['id']}", "original_value": ch["from"],
                             "transform": f"human edit of {eid} ({field}): {ch['from']!r} -> {ch['to']!r}",
                             "verification": "unverified"}
        self.spec["evidence"].append(new)
        self.evidence[new_id] = new
        for r in s["evidence"]:
            if r["ref"] == eid:
                r["ref"] = new_id
                r["decided_by"] = ["HUMAN"]
        self.evidence[eid]["disposition"] = "supporting" if self.evidence[eid]["disposition"] == "primary" else \
            self.evidence[eid]["disposition"]
        for m in self.meta.values():  # later edits to the same element now refer to the new item
            if m.get("slide") == sid and m.get("evidence") == eid:
                m["evidence"] = new_id
        self.log("data_edit", {"type": "evidence", "id": new_id},
                 f"{eid} {field} edited on slide {sid}: {ch['from']!r} -> {ch['to']!r}. Stored as new unverified "
                 f"evidence {new_id}, derived from {eid}; {eid} is unchanged.", True, rules=["EVD-34", "EVD-03"])

    # ---------- structure ----------
    def slide_deleted(self, page):
        sid = _slide_of_page(page)
        if not sid or sid not in self.slides:
            return
        s = self.slides.pop(sid)
        self.spec["slides"] = [x for x in self.spec["slides"] if x["id"] != sid]
        self.spec.setdefault("retired_slides", []).append(dict(s, retired="deleted in Google Slides"))
        for r in s.get("evidence", []):
            ev = self.evidence.get(r["ref"])
            if ev and not any(r["ref"] == q["ref"] for x in self.spec["slides"] for q in x.get("evidence", [])):
                ev["disposition"] = "background"
        self.log("slide_deleted", {"type": "slide", "id": sid},
                 f"{sid} removed from the deck. Its claim and evidence stay in the registries; the slide is kept in "
                 f"retired_slides.", False)
        arg = self._arg_of(s)
        if arg and arg["role"] in ("primary", "supporting"):
            self.diverge("deleted premise", sid, f"The slide carrying {arg['id']} ({arg['role']}) was deleted: "
                                                 f"{arg['statement']}", {"type": "slide_deleted", "id": page})

    def reorder(self, new_pages):
        order = [p for p in (_slide_of_page(x) for x in new_pages) if p in self.slides]
        old = [s["id"] for s in self.spec["slides"]]
        rest = [x for x in old if x not in order]
        self.spec["slides"] = [self.slides[x] for x in order + rest]
        self.log("slides_reordered", {"type": "deck", "id": self.spec["deck_id"]}, " ".join(order), False,
                 rationale=f"was: {' '.join(old)}")
        step = {}
        for st in self.narrative.get("narrative_progression", []):
            for aid in st.get("arguments", []):
                step.setdefault(aid, st.get("step", 0))
        seq = [(sid, (self._arg_of(self.slides[sid]) or {}).get("id")) for sid in order]
        seq = [(sid, aid, step[aid]) for sid, aid in seq if aid in step]
        for (s1, a1, st1), (s2, a2, st2) in zip(seq, seq[1:]):
            if st2 < st1:
                self.diverge("logic-breaking reorder", s2,
                             f"{a2} (progression step {st2}) now comes after {a1} (step {st1}); the approved "
                             f"narrative introduces {a2} first.", {"type": "slides_reordered"})

    def element_deleted(self, oid_):
        info = self.meta.get(oid_, {})
        sid = info.get("slide")
        s = self.slides.get(sid)
        if not s:
            return
        if info.get("role") == "data" and info.get("evidence"):
            for r in s["evidence"]:
                if r["ref"] == info["evidence"]:
                    r["placement"] = "notes"
                    r["decided_by"] = ["HUMAN"]
            return self.log("evidence_removed_from_slide", {"type": "evidence", "id": info["evidence"]},
                            f"Evidence drawing deleted on {sid}; the item moves to the notes and stays in the registry.",
                            True, rules=["EVD-05"])
        if info.get("maps_to") == "slide.headline":
            s["headline"] = ""
            self.judge_queue.append({"id": oid_, "slide": sid, "kind": "headline", "from": "(headline)", "to": ""})
            return
        if info.get("role") == "text":
            return self.text_edit({"id": oid_, "from": "", "to": ""}, info)
        self.log("element_deleted", {"type": "element", "id": oid_}, "removed", False)

    def element_added(self, ch, dup_of=None):
        sid = _slide_of_page(ch["slide"])
        if dup_of:
            return self.log("duplicate_element", {"type": "element", "id": ch["id"]},
                            f"Duplicate of {dup_of} on {sid}; not matched to the original.", True)
        s = self.slides.get(sid)
        if s is None:
            return
        s.setdefault("human_elements", []).append({"id": ch["id"], "text": ch.get("text", "")})
        if ch.get("text", "").strip():
            self.judge_queue.append({"id": ch["id"], "slide": sid, "kind": "new_text", "from": "", "to": ch["text"]})
        self.log("element_added", {"type": "slide", "id": sid}, ch.get("text", "") or "(non-text element)", True,
                 rationale="Added in Google Slides; recorded on the slide as a human element.")

    def slide_added(self, page, elements):
        alts = [e["alt"] for e in elements.values() if e["slide"] == page and e.get("alt", "").startswith("deck-tool:")]
        src = alts[0].split(":", 1)[1].split("/")[0] if alts else None
        if src:
            return self.log("duplicate_slide", {"type": "slide", "id": page},
                            f"Slide duplicated from {src}. Duplicates get new object ids; not merged into the spec.", True)
        texts = [e["text"] for e in elements.values() if e["slide"] == page and e["text"]]
        self.spec.setdefault("human_slides", []).append({"page": page, "texts": texts})
        self.log("slide_added", {"type": "slide", "id": page}, " | ".join(texts)[:300] or "(no text)", True)
        if texts:
            self.judge_queue.append({"id": page, "slide": page, "kind": "new_text", "from": "", "to": " ".join(texts)})

    # ---------- narrative divergence ----------
    def judge(self):
        if not self.judge_queue:
            return
        items = []
        for q in self.judge_queue:
            s = self.slides.get(q["slide"], {})
            arg = self._arg_of(s) if s else None
            ev = [self.evidence[r["ref"]] for r in s.get("evidence", []) if r["ref"] in self.evidence] if s else []
            items.append({**q, "argument": (arg or {}).get("statement", ""),
                          "evidence": [{"id": e["id"], "text": e.get("text_read") or e.get("text_live", ""),
                                        "verification": e.get("provenance", {}).get("verification", "")} for e in ev]})
        verdicts = judge_changes(items, self.narrative)
        by_id = {v["id"]: v for v in verdicts}
        for q in self.judge_queue:
            v = by_id.get(q["id"], {"material": True, "kind": "unjudged", "supported": False,
                                    "reason": "no verdict returned; flagged to be safe"})
            material = v.get("material") or (q["kind"] == "new_text" and not v.get("supported", True))
            if q["kind"] == "headline":
                self.log("headline_edit", {"type": "slide", "id": q["slide"]}, q["to"], bool(material),
                         rationale=f"was: {q['from']!r}. Judged {v.get('kind')}: {v.get('reason', '')}")
            if material:
                kind = {"headline": "material claim change", "new_text": "unsupported new conclusion"}[q["kind"]]
                self.diverge(kind, q["slide"], f"{v.get('kind')}: {v.get('reason', '')}",
                             {"id": q["id"], "from": q["from"], "to": q["to"]})

    # ---------- run ----------
    def run_changes(self, changes, current):
        self.current = current
        alts = {e.get("alt") for e in self.snap["elements"].values() if e.get("alt")}
        for ch in changes:
            t = ch["type"]
            if t == "text_edited":
                info = self.meta.get(ch["id"])
                if info is None:
                    self.log("unhandled_edit", {"type": "element", "id": ch["id"]}, ch["to"], True)
                elif info.get("role") == "data":
                    self.data_edit(ch, info)
                elif info.get("role") == "risk":
                    s = self.slides.get(info["slide"])
                    row = next((r for r in (s or {}).get("risks", []) if r["id"] == info["risk"]), None)
                    if row is not None:
                        row[info["field"]] = ch["to"]
                        self.log("risk_row_edit", {"type": info["field"], "id": info["risk"]}, ch["to"], False)
                else:
                    self.text_edit(ch, info)
            elif t == "slide_deleted":
                self.slide_deleted(ch["id"])
            elif t == "slides_reordered":
                pass  # applied once below, from the full current order
            elif t == "element_deleted":
                self.element_deleted(ch["id"])
            elif t == "element_added":
                if _slide_of_page(ch["slide"]):
                    alt = current.get(ch["id"], {}).get("alt", "")
                    self.element_added(ch, dup_of=alt if alt in alts else None)
            elif t == "slide_added_or_duplicated":
                self.slide_added(ch["id"], current)
            elif t == "element_moved_slide":
                self.log("element_moved", {"type": "element", "id": ch["id"]}, f"{ch['from']} -> {ch['to']}", True)
        if any(c["type"] == "slides_reordered" for c in changes):
            self.reorder(self.current_order)
        self.judge()


def judge_changes(items: list[dict], narrative: dict) -> list[dict]:
    """Batched judgment: does each edit change the argument? Stub: a change is material
    when its numbers change or under half its words survive."""
    if llm_mode() == "stub":
        out = []
        for it in items:
            a, b = set(it["from"].lower().split()), set(it["to"].lower().split())
            nums_changed = set(_NUM.findall(it["from"])) != set(_NUM.findall(it["to"]))
            overlap = len(a & b) / max(1, len(a | b))
            material = it["kind"] == "new_text" or nums_changed or overlap < 0.5
            out.append({"id": it["id"], "material": material, "kind": "different" if material else "wording",
                        "supported": it["kind"] != "new_text", "reason": "stub heuristic"})
        return out
    data = call_json(fill("judge_sync.md", THESIS=narrative.get("thesis", ""), ITEMS=items),
                     schema={"type": "object", "properties": {"verdicts": {"type": "array", "items": {
                         "type": "object", "properties": {"id": {"type": "string"}, "material": {"type": "boolean"},
                                                          "kind": {"type": "string"}, "supported": {"type": "boolean"},
                                                          "reason": {"type": "string"}},
                         "required": ["id", "material", "kind", "supported", "reason"], "additionalProperties": False}}},
                         "required": ["verdicts"], "additionalProperties": False})
    return data.get("verdicts", [])


def sync_deck(deck_id: str, presentation: dict | None = None) -> dict:
    """Read the live deck (or an injected presentations.get response, for tests), apply
    changes to the deck spec, and save spec, divergences, and a new snapshot."""
    from .deck import read_deck
    run = run_dir(deck_id)
    snap = load_json(run / "slides_map.json")
    if not snap or "meta" not in snap:
        raise SystemExit(f"No full-deck snapshot for {deck_id}. Run: python3 deck.py gslides create-deck <spec.json>")
    spec_path = snap["spec_path"]
    spec = load_json(spec_path)
    if presentation is None:
        from .auth import slides_service
        presentation = slides_service().presentations().get(presentationId=snap["presentation_id"]).execute()
    order, elements = read_deck(presentation)
    changes = diff(snap, order, elements)
    narrative = load_json(run / "narrative_spec.json", {})
    sy = Sync(spec, snap, narrative, run)
    sy.current_order = order
    sy.run_changes(changes, elements)
    errors = validate(sy.spec)
    if errors:
        raise SystemExit("Synced spec failed validation:\n" + "\n".join(f"  {e}" for e in errors))
    save_json(spec_path, sy.spec)
    if sy.divergence:
        prior = load_json(run / "narrative_divergence.json", [])
        save_json(run / "narrative_divergence.json", prior + sy.divergence)
    new_snap = dict(snap, slide_order=order, elements=elements, meta=sy.meta, ts=time.strftime("%Y-%m-%dT%H:%M:%S"))
    save_json(run / "slides_map.json", new_snap)
    save_json(run / "snapshots" / f"{new_snap['ts'].replace(':', '')}.json", new_snap)
    return {"changes": changes, "applied": sy.applied, "flagged": sy.flagged, "divergence": sy.divergence}
