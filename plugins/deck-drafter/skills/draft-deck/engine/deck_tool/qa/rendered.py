"""
Milestone 8: rendered-output QA, the third QA stage after deterministic lint and
judgment QA on the spec (docs/GOOGLE_SLIDES_DESIGN.md section 6). It looks at what was
actually drawn, from slide images.

Two passes, reported separately so it is always clear which kind of check found what:

1. Deterministic, on pixels. Every layout keeps a clear band along the bottom and
   right edges (below the source line and slide number, right of the content width).
   Ink in those bands means something was drawn past its box: text that overflowed,
   a chart spilling out. The background colour is read from the slide's own corners,
   so dark title and closing slides are handled the same way.
2. Model judgment, on images: the CLI reads each image and reports clipped or
   overlapping text, illegible charts, and empty evidence regions. Optional, since it
   costs a model call; the deterministic pass always runs.

Images come from Google Slides thumbnails (deck_tool.gslides.preview) or LibreOffice
where installed. Each finding is logged as a review-required RENDERED decision.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .. import decisions
from ..common import call_json, llm_mode

# Clear bands as fractions of the slide: the source line ends at 7.23 in of 7.5 (96.4%)
# and content stops at 12.583 in of 13.333 (94.4%). The bands start just past those.
BOTTOM_BAND = (0.975, 1.0)
RIGHT_BAND = (0.965, 1.0)
INK_THRESHOLD = 0.004   # share of band pixels that differ from the background
COLOR_TOLERANCE = 40
EDGE_PX = 2               # outermost pixels ignored (rasteriser edge antialiasing)     # summed RGB distance treated as "the same colour" (antialiasing)


def _background(img):
    w, h = img.size
    corners = [img.getpixel((x, y)) for x in (2, w - 3) for y in (2, h - 3)]
    return Counter(c[:3] for c in corners).most_common(1)[0][0]


def _ink_share(img, box, bg) -> float:
    x0, y0, x1, y1 = box
    region = img.crop((x0, y0, x1, y1))
    px = list(region.getdata())
    if not px:
        return 0.0
    diff = sum(1 for p in px if sum(abs(a - b) for a, b in zip(p[:3], bg)) > COLOR_TOLERANCE)
    return diff / len(px)


def edge_findings(image_path, slide_id: str) -> list[dict]:
    from PIL import Image
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    bg = _background(img)
    out = []
    # The outermost EDGE_PX rows and columns are left out: a rasteriser converting a page
    # whose width is not a whole number of pixels (13.333 in at 80 dpi is 1066.7 px)
    # antialiases the last column against white, which reads as ink on dark slides.
    m = EDGE_PX
    bands = {"bottom edge": (0, int(h * BOTTOM_BAND[0]), w - m, h - m),
             "right edge": (int(w * RIGHT_BAND[0]), 0, w - m, int(h * BOTTOM_BAND[0]))}
    for name, box in bands.items():
        share = _ink_share(img, box, bg)
        if share > INK_THRESHOLD:
            out.append({"slide": slide_id, "check": "edge_ink", "severity": "block",
                        "problem": f"Content drawn in the clear band along the {name} ({share:.1%} of the band), "
                                   f"so something overflowed its box.",
                        "fix": "Shorten the text, split the slide, or move content to the appendix."})
    return out


VISION_PROMPT = """You are the rendered-output check of an AI deck-building tool. Read each slide image listed below with the Read tool and look at what was actually drawn.

For each slide, report only real rendering defects a viewer would notice: text cut off or running past the slide edge, text overlapping other text or shapes, a chart or diagram that is illegible or empty, an evidence region left blank, or text too small to read when projected. Do not comment on wording, argument, or design taste.

Slides:
{listing}

Return only a JSON object, no prose and no code fences:
{{"findings": [{{"slide": "SL04", "problem": "one sentence", "fix": "one sentence"}}]}}
Return {{"findings": []}} if nothing is wrong."""


def vision_findings(images: list[tuple[str, Path]], batch: int = 8) -> list[dict]:
    if llm_mode() == "stub":
        return []
    from .. import common
    out = []
    saved = list(common.CLAUDE_EXTRA_ARGS)
    common.CLAUDE_EXTRA_ARGS[:] = saved + ["--allowedTools", "Read"]
    try:
        for i in range(0, len(images), batch):
            chunk = images[i:i + batch]
            listing = "\n".join(f"- {sid}: {Path(p).resolve()}" for sid, p in chunk)
            data = call_json(VISION_PROMPT.format(listing=listing))
            for f in data.get("findings", []):
                out.append({"slide": f.get("slide", ""), "check": "vision", "severity": "warn",
                            "problem": f.get("problem", ""), "fix": f.get("fix", "")})
    finally:
        common.CLAUDE_EXTRA_ARGS[:] = saved
    return out


def rendered_qa(images: list[tuple[str, Path]], run=None, use_model: bool = False) -> dict:
    """images: [(slide id, image path)] in deck order."""
    det = [f for sid, p in images for f in edge_findings(p, sid)]
    vis = vision_findings(images) if use_model else []
    if run is not None:
        decisions.clear_stage(run, "rendered_qa")
        for f in det + vis:
            decisions.log(run, stage="rendered_qa", source="RENDERED", action=f["check"],
                          target={"type": "slide", "id": f["slide"]}, result=f["problem"], rationale=f["fix"],
                          review_required=True)
    return {"deterministic": det, "model": vis, "slides": len(images), "model_run": use_model}


def write_report(run, result: dict, source: str):
    lines = [f"# Rendered QA report: {Path(run).name}", "",
             f"{result['slides']} slide image(s) from {source}. Deterministic edge check: "
             f"{len(result['deterministic'])} finding(s). Model image review: "
             + (f"{len(result['model'])} finding(s)." if result["model_run"] else "not run."), ""]
    for title, key in (("Deterministic (pixels)", "deterministic"), ("Model judgment (images)", "model")):
        if result[key]:
            lines += [f"## {title}", "", "| Slide | Problem | Fix |", "|---|---|---|"]
            lines += [f"| {f['slide']} | {f['problem']} | {f['fix']} |" for f in result[key]]
            lines.append("")
    (Path(run) / "rendered_qa_report.md").write_text("\n".join(lines) + "\n")
