"""
Milestone 3's narrow slice of sync: apply a hand-edited headline, read back via
deck_tool.gslides.spike.diff(), into the persistent v0.2 spec -- without touching the
claims or evidence registries. This is not the full Milestone 7 sync engine; it closes
just enough of the loop for the brief's acceptance test (create, hand-edit a headline,
read back, match the edit to the right internal ID, update the spec without losing the
original claim or evidence).

Only applies to placeholders literally named "headline" (dt_<slide_id>_headline), which
is every vertical-slice layout except title.A (whose headline placeholder is named
"title"). Edits to title.A's text, or anything else diff() reports, are returned
unhandled rather than guessed at.
"""
from __future__ import annotations

import re

from .. import decisions

_HEADLINE_RE = re.compile(r"^dt_(SL\d+)_headline$")


def apply_headline_edit(spec_v2: dict, change: dict, run) -> dict:
    """Apply one diff()-produced text_edited change on a slide's 'headline' placeholder
    into the persistent v0.2 spec. Updates only slides[i]['headline']; claims[] and
    evidence[] are untouched. Logs one review-required HUMAN decision. Returns a new
    spec dict (slides list rebuilt; everything else is the same object)."""
    if change.get("type") != "text_edited":
        raise ValueError(f"not a text_edited change: {change}")
    m = _HEADLINE_RE.match(change.get("id", ""))
    if not m:
        raise ValueError(f"not a headline placeholder id: {change.get('id')}")
    slide_id = m.group(1)

    slides = [dict(s) for s in spec_v2["slides"]]
    idx = next(i for i, s in enumerate(slides) if s["id"] == slide_id)
    slides[idx]["headline"] = change["to"]

    decisions.log(run, stage="gslides_sync", source="HUMAN", action="headline_edit",
                  target={"type": "slide", "id": slide_id}, result=change["to"],
                  rationale=f"Hand-edited in Google Slides (was: {change['from']!r})",
                  review_required=True)

    out = dict(spec_v2)
    out["slides"] = slides
    return out


def apply_headline_edits(spec_v2: dict, changes: list[dict], run) -> tuple[dict, list[dict]]:
    """Apply every headline-placeholder edit in changes; return (updated_spec, the rest,
    unhandled and unmodified)."""
    unhandled = []
    for ch in changes:
        if ch.get("type") == "text_edited" and _HEADLINE_RE.match(ch.get("id", "")):
            spec_v2 = apply_headline_edit(spec_v2, ch, run)
        else:
            unhandled.append(ch)
    return spec_v2, unhandled
