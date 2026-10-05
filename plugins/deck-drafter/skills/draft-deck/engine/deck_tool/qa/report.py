from __future__ import annotations

from collections import Counter

from .lint import as_dicts


def write(run, spec, lint_findings, judge_findings, skipped, exemptions, judge_note):
    allf = lint_findings + judge_findings
    data = {"deck_id": spec["deck_id"], "findings": as_dicts(allf), "skipped": skipped, "exemptions": exemptions}
    md = [f"# QA report: {spec['deck_id']}", ""]
    sev = Counter(f.severity for f in allf)
    src = Counter(f.source for f in allf)
    md += [f"Findings: {sev['block']} blocking, {sev['warn']} warnings, {sev['advise']} advisory. "
           f"By source: " + ", ".join(f"{k} {n}" for k, n in sorted(src.items())) + ".", "", judge_note, ""]
    for level, title in (("block", "Blocking"), ("warn", "Warnings"), ("advise", "Advisory")):
        sel = [f for f in allf if f.severity == level]
        if not sel:
            continue
        md += [f"## {title}", "", "| Slide | Field | Variant | Source | Rules | Problem | Fix |", "|---|---|---|---|---|---|---|"]
        for f in sel:
            md.append(f"| {f.slide or 'deck'} | {f.field or ''} | {'/'.join(f.variants)} | {f.source} | {', '.join(f.rules)} | "
                      f"{f.message.replace('|', '/')} | {f.fix.replace('|', '/')} |")
        md.append("")
    if exemptions:
        md += ["## Brand exemptions applied", ""] + [f"- {e}" for e in exemptions] + [""]
    if skipped:
        md += ["## Checks skipped for this profile", ""] + [f"- {s}" for s in skipped] + [""]
    from ..common import save_json
    save_json(run / "qa_report.json", data)
    (run / "qa_report.md").write_text("\n".join(md) + "\n")
    return sev
