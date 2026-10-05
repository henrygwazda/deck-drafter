"""
Shared decision log. Every stage that changes or chooses something appends an entry
naming the rule(s) behind it and, where a brand entry changed the outcome, what the
KB alone would have done. That pairing is what lets a reviewer judge whether the
brand layer (or the KB) is earning its keep.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path

LOG = "decision_log.jsonl"


def _path(run: Path) -> Path:
    return Path(run) / LOG


def read(run: Path) -> list[dict]:
    p = _path(run)
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def write_all(run: Path, entries: list[dict]):
    _path(run).write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))


def clear_stage(run: Path, stage: str):
    """Re-running a stage replaces its entries but keeps reviews already given on identical decisions."""
    write_all(run, [e for e in read(run) if e["stage"] != stage])


def log(run: Path, *, stage: str, source: str, action: str, target: dict, result: str,
        rationale: str = "", kb_rules=(), brand_rules=(), kb_default: str | None = None,
        variant: str = "both", review_required: bool = False, prior_reviews: dict | None = None) -> dict:
    """Append a decision. An identical decision already logged for another variant is merged
    into one entry, so a reviewer judges each choice once rather than once per variant."""
    entries = read(run)
    key = f"{stage}|{target.get('type')}|{target.get('id')}|{action}|{','.join(brand_rules)}"
    for e in entries:
        if e["key"] == key and e["result"] == result:
            if variant not in e["variants"]:
                e["variants"].append(variant)
                write_all(run, entries)
            return e
    e = {
        "id": f"D{max([int(x['id'][1:]) for x in entries] + [0]) + 1:04d}",
        "key": key,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "stage": stage,
        "variants": [variant],
        "source": source,
        "action": action,
        "target": target,
        "kb_rules": list(kb_rules),
        "brand_rules": list(brand_rules),
        "kb_default": kb_default,
        "result": result,
        "rationale": rationale,
        "review_required": review_required,
        "review": (prior_reviews or {}).get(key, {"verdict": None, "value": None, "note": None}),
    }
    with open(_path(run), "a") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return e


def reviews_by_key(run: Path) -> dict:
    return {e["key"]: e["review"] for e in read(run) if e.get("review", {}).get("verdict")}


def unreviewed(run: Path) -> list[dict]:
    return [e for e in read(run) if e["review_required"] and not e["review"].get("verdict")]


def interactive_review(run: Path):
    entries = read(run)
    todo = [e for e in entries if e["review_required"] and not e["review"].get("verdict")]
    print(f"{len(todo)} decisions need review. Enter a/r/m (accept, reject, modify), then h/n/x for value "
          f"(helped, neutral, hurt), optional note. Blank verdict skips, q quits.\n")
    for e in todo:
        print(f"[{e['id']}] {e['stage']} / {'+'.join(e['variants'])} / {e['source']}  {e['action']} -> {e['target']}")
        if e.get("kb_default"):
            print(f"  KB alone:  {e['kb_default']}")
        print(f"  Result:    {e['result']}")
        print(f"  Why:       {e['rationale']}  rules: {', '.join(e['kb_rules'] + e['brand_rules'])}")
        v = input("  verdict> ").strip().lower()
        if v == "q":
            break
        if v not in ("a", "r", "m"):
            continue
        val = input("  value>   ").strip().lower()
        note = input("  note>    ").strip()
        e["review"] = {"verdict": {"a": "accept", "r": "reject", "m": "modify"}[v],
                       "value": {"h": "helped", "n": "neutral", "x": "hurt"}.get(val), "note": note or None}
        print()
    write_all(run, entries)


def report(run: Path) -> str:
    entries = read(run)
    md = [f"# Decision report: {Path(run).name}", ""]
    by_src = Counter(e["source"] for e in entries)
    md.append("Decisions by source: " + ", ".join(f"{k} {v}" for k, v in sorted(by_src.items())) + ".")
    rev = [e for e in entries if e["review_required"]]
    done = [e for e in rev if e["review"].get("verdict")]
    md += [f"Review required: {len(rev)}. Reviewed: {len(done)}.", ""]

    # value of each brand entry and KB rule, from reviews
    tally = defaultdict(Counter)
    for e in done:
        for rid in e["brand_rules"] or e["kb_rules"]:
            tally[rid][e["review"].get("value") or "unrated"] += 1
            tally[rid][e["review"]["verdict"]] += 1
    if tally:
        md += ["## Value by rule (from reviews)", "", "| Rule | accept | reject | modify | helped | neutral | hurt |",
               "|---|---|---|---|---|---|---|"]
        for rid, c in sorted(tally.items()):
            md.append(f"| {rid} | {c['accept']} | {c['reject']} | {c['modify']} | {c['helped']} | {c['neutral']} | {c['hurt']} |")
        md.append("")

    def row(e):
        r = e["review"]
        return "| {id} | {stage} | {action} | {t} | {rules} | {res} | {rv} |".format(
            id=e["id"], stage=e["stage"], action=e["action"], t=f"{e['target'].get('type')} {e['target'].get('id')}",
            rules=", ".join(e["brand_rules"] + e["kb_rules"]) or e["source"],
            res=e["result"].replace("|", "/").replace("\n", " ")[:200],
            rv=(r.get("verdict") or "pending") + (f" ({r['value']})" if r.get("value") else ""))

    head = "| ID | Stage | Action | Target | Rules or source | Result | Review |"
    sep = "|---|---|---|---|---|---|---|"
    # 1. Everything a human must rule on, whatever produced it (model, KB, template, brand).
    if rev:
        md += ["## Decisions needing a human verdict", "",
               "Step through these with `python3 deck.py review <deck_id>`.", "", head, sep]
        md += [row(e) for e in rev]
        md.append("")
    # 2. Brand changes, with what the KB alone would have done.
    brand = [e for e in entries if "BRAND" in e["source"]]
    if brand:
        md += ["## Brand-driven decisions", "", "| ID | Stage | Variant | Action | Target | Rules | KB alone would have | Result |",
               "|---|---|---|---|---|---|---|---|"]
        for e in brand:
            md.append("| {id} | {stage} | {variant} | {action} | {t} | {rules} | {kb} | {res} |".format(
                id=e["id"], stage=e["stage"], variant="+".join(e["variants"]), action=e["action"],
                t=f"{e['target'].get('type')} {e['target'].get('id')}", rules=", ".join(e["brand_rules"] + e["kb_rules"]),
                kb=(e.get("kb_default") or "").replace("|", "/")[:160], res=e["result"].replace("|", "/")[:160]))
        md.append("")
    # 2b. Persuasion-layer changes, with what the KB alone would have done (kb/persuasion_v1.json).
    pers = [e for e in entries if e["source"] == "PERSUASION"]
    if pers:
        md += ["## Persuasion-driven decisions", "", "| ID | Stage | Action | Target | Rules | KB alone would have | Result |",
               "|---|---|---|---|---|---|---|"]
        for e in pers:
            md.append("| {id} | {stage} | {action} | {t} | {rules} | {kb} | {res} |".format(
                id=e["id"], stage=e["stage"], action=e["action"], t=f"{e['target'].get('type')} {e['target'].get('id')}",
                rules=", ".join(e["kb_rules"]), kb=(e.get("kb_default") or "").replace("|", "/")[:160],
                res=e["result"].replace("|", "/")[:160]))
        md.append("")
    # 3. The editorial choices that shaped the deck and did not need review, with their rules.
    meaningful = {"select_layout", "split_slide", "state_caveat", "route_to_appendix", "risk_live",
                  "promote_to_live", "demote_to_appendix", "headline_edit"}
    chosen = [e for e in entries if not e["review_required"] and e["action"] in meaningful]
    if chosen:
        md += ["## Editorial choices", "", head, sep]
        md += [row(e) for e in chosen]
        md.append("")
    # 4. Everything else, counted rather than listed, so hundreds of incidental rule
    # activations do not bury the choices above.
    info = Counter((e["stage"], e["action"]) for e in entries
                   if not e["review_required"] and e["action"] not in meaningful and "BRAND" not in e["source"]
                   and e["source"] != "PERSUASION")
    if info:
        md += ["## Incidental decisions (counted, not listed)", ""]
        md += [f"- {stage}: {action} x{n}" for (stage, action), n in sorted(info.items())]
    return "\n".join(md) + "\n"
