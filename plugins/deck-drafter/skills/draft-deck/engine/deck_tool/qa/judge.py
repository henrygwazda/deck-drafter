"""
Judgment QA. Sends slides, in small batches, to the model with only the active QA rules
that the linter can't check mechanically. A separate deck-level pass reads the headlines
in sequence. Use --dry-run to write the prompts to disk without calling the model.
"""
from __future__ import annotations

import json

from ..common import call_json, fill, llm_mode
from .lint import KB_LINTED, Finding, merge_variants

DECK_RULES = {"NAR-01", "NAR-16", "NAR-19", "NAR-21", "CLM-24", "CLM-28", "NAR-35", "NAR-09", "FAIL-10"}
BATCH = 4


def slide_view(slide, v):
    s = {k: val for k, val in slide.items() if k not in ("body",)}
    s["body"] = slide.get("body", {}).get(v)
    s["evidence"] = [{k: val for k, val in e.items() if k != ("text_read" if v == "live" else "text_live")}
                     for e in slide.get("evidence", [])]
    return s


def judge_rules(data, deck_level=False):
    lines = []
    for rid, s in sorted(data["rules"].items()):
        if s["status"] == "inactive" or "qa" not in s["stages"]:
            continue
        if (rid in DECK_RULES) != deck_level or rid in KB_LINTED:
            continue
        tag = {"overridden": " brand override", "modified": " brand-modified"}.get(s["status"], "")
        lines.append(f"{rid} [{s['severity']}{tag}] {s['text']}")
    for bid, b in sorted(data["brand_rules"].items()):
        if "qa" in b["stages"] and not b.get("lint") and not deck_level:
            lines.append(f"{bid} [brand {b['kind']}, {b['severity']}] {b['text']}")
        if deck_level and bid in ("BRD-21", "BRD-22", "BRD-06", "BRD-12"):
            lines.append(f"{bid} [brand {b['kind']}, {b['severity']}] {b['text']}")
    return lines


def judge(spec, active, run, dry_run=False):
    out_dir = run / "qa_prompts"
    out_dir.mkdir(exist_ok=True)
    findings, prompts = [], []
    profile = {k: v for k, v in active["profile"].items() if k in ("deck_type", "purpose", "audience", "delivery", "channels")}
    for v, data in active["variants"].items():
        rules = "\n".join(judge_rules(data))
        slides = spec["slides"]
        for i in range(0, len(slides), BATCH):
            batch = [slide_view(s, v) for s in slides[i:i + BATCH]]
            prompts.append((f"{v}_slides_{i // BATCH + 1:02d}", v, fill(
                "qa_slides.md", THESIS=spec["thesis"], VARIANT=v, PROFILE=profile, RULES=rules, SLIDES=batch)))
        titles = "\n".join(f"{s['id']} ({s['layout']}, {s.get('section')}): {s.get('headline', '')}" for s in slides)
        prompts.append((f"{v}_deck", v, fill("qa_deck.md", THESIS=spec["thesis"], VARIANT=v, PROFILE=profile,
                                             RULES="\n".join(judge_rules(data, deck_level=True)), TITLES=titles,
                                             SECTIONS=spec.get("sections", []))))

    for name, v, prompt in prompts:
        (out_dir / f"{name}.md").write_text(prompt)
    if dry_run or llm_mode() == "stub":
        print(f"judge: wrote {len(prompts)} prompts to {out_dir} (not sent)")
        return [], len(prompts)

    for name, v, prompt in prompts:
        data = active["variants"][v]
        res = call_json(prompt)
        for f in res.get("findings", []):
            rid = f.get("rule", "")
            st = data["rules"].get(rid) or data["brand_rules"].get(rid) or {}
            src = "BRAND" if rid.startswith("BRD") else ("KB+BRAND" if st.get("brand") else "KB")
            findings.append(Finding(check="judge", severity=st.get("severity", "warn"), source=src, rules=[rid],
                                    message=f.get("problem", ""), slide=f.get("slide"), field=f.get("field"),
                                    variants=[v], fix=f.get("fix", "")))
        print(f"judge: {name} -> {len(res.get('findings', []))} findings")
    return merge_variants(findings), len(prompts)
