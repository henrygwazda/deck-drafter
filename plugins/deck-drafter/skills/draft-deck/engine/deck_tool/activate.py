"""
Profile and activation stage.

Reads a deck profile, the curated KB, and a brand layer, and writes
runs/<deck_id>/active_rules.json: for each delivery variant (live, read), which rules
are active, inactive, conditional, overridden, or modified by brand, with a reason
and severity for every one. Downstream stages load rules from this file by stage, so
each prompt sees only what applies to this deck.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import decisions
from .common import ROOT, call_json, fill, llm_mode, load_json, run_dir, save_json, sha
from .kb import KB_BLOCKING, SEVERITY, brand_in_scope, load_brand, load_kb, max_sev

STAGES = ["message_model", "outline", "slide_spec", "editorial", "renderer", "notes", "qa"]
# Both a faster model (Haiku) and a larger batch (80) were tried for this stage and
# rejected, each on real side-by-side comparisons against cached Sonnet-at-40
# classifications: both show the same systematic bias toward "active"/"partial" under
# any ambiguity ("unknown; default active") rather than confidently ruling a rule out
# the way Sonnet-at-40 does from the profile's own stated facts -- directly undermining
# activation's purpose of narrowing rule load to what a deck actually needs. See
# docs/ACTIVATION_MODEL_VALIDATION.md.
BATCH = 40
CLASSIFY_MODEL = "sonnet"
UNCONDITIONAL = re.compile(r"^always\b", re.I)
HEDGED = re.compile(r"specifically|only|versus|split by|condition on|except", re.I)


def variants(profile) -> list[str]:
    mode = profile["delivery"]["mode"]
    return ["live", "read"] if mode == "both" else [mode]


def variant_profile(profile, v):
    p = {k: val for k, val in profile.items() if k not in ("brand", "notes")}
    p["variant"] = v
    p["delivery"] = dict(profile["delivery"], mode=v)
    return p


# ---------- offline stub (DECK_TOOL_LLM=stub) ----------

LIVE_WORDS = re.compile(r"\blive\b|presented|projected|narrat|in the room|speaker|animation|build or reveal", re.I)
READ_WORDS = re.compile(r"read without|read-alone|slidedoc|sent|pre-read|leave-behind|async|handout|read independently", re.I)


def stub_classify(rule, vp):
    """Crude keyword heuristics so the pipeline can be exercised without model calls."""
    a = rule["applies_when"]
    v, d, aud = vp["variant"], vp["delivery"], vp["audience"]
    live, read = bool(LIVE_WORDS.search(a)), bool(READ_WORDS.search(a))
    if live and not read and v == "read":
        return "inactive", "stub: live-delivery condition, read variant"
    if read and not live and v == "live" and not d.get("pre_read"):
        return "inactive", "stub: read-alone condition, live variant"
    if re.search(r"remote|webinar|teleconference|virtual", a, re.I) and d.get("setting") != "virtual":
        return "inactive", "stub: remote-delivery condition, in-person setting"
    if re.search(r"large venue|large-group", a, re.I) and d.get("setting") != "large_venue":
        return "inactive", "stub: large-venue condition"
    if re.search(r"non-native", a, re.I) and not aud.get("non_native_speakers"):
        return "inactive", "stub: no non-native speakers recorded"
    if re.search(r"recurring (status|update)|status or update|progress or status update", a, re.I) and vp["deck_type"] != "update":
        return "inactive", "stub: recurring-update condition, deck is not an update"
    if re.search(r"advisory/educational|educational or explanatory", a, re.I):
        return "inactive", "stub: educational condition, deck is a funding ask"
    if re.search(r"live demo|complex equipment", a, re.I):
        return "inactive", "stub: no live demo planned"
    if HEDGED.search(a):
        return "partial", "stub: hedged condition, treated as partly applicable"
    return "active", "stub: no disqualifying condition detected"


# ---------- activation ----------

UNCLASSIFIED_REASON = ("not classified against the profile (no-model draft mode): kept in force as "
                       "conditional rather than switched off")


def classify(rules, vp, cache, model: str = CLASSIFY_MODEL, unclassified: bool = False):
    """unclassified: skip the model and keep every conditional rule as "partial"
    (reported as conditional). Used by the plugin's draft mode, where no model call is
    available to the engine: it errs toward applying a rule, never toward dropping one."""
    results, todo = {}, []
    for r in rules.values():
        a = r.get("applies_when", "")
        if UNCONDITIONAL.match(a.strip()) and not HEDGED.search(a):
            results[r["id"]] = {"status": "active", "reason": "unconditional", "scope_note": ""}
            continue
        key = sha([vp, r["id"], a])
        if key in cache:
            results[r["id"]] = cache[key]
        else:
            todo.append((key, r))

    if unclassified:
        for key, r in todo:
            results[r["id"]] = {"status": "partial", "reason": UNCLASSIFIED_REASON, "scope_note": ""}
        return results

    if llm_mode() == "stub":
        for key, r in todo:
            s, why = stub_classify(r, vp)
            cache[key] = results[r["id"]] = {"status": s, "reason": why, "scope_note": ""}
        return results

    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        payload = [{"id": r["id"], "applies_when": r["applies_when"], "rule": r["rule"][:220]} for _, r in chunk]
        data = call_json(fill("activate.md", PROFILE=vp, RULES=payload), model=model)
        got = {x["id"]: x for x in data.get("results", [])}
        for key, r in chunk:
            x = got.get(r["id"], {"status": "active", "reason": "model omitted this rule; defaulted active"})
            entry = {"status": x.get("status", "active"), "reason": x.get("reason", ""),
                     "scope_note": x.get("scope_note", "")}
            cache[key] = results[r["id"]] = entry
        print(f"  classified {min(i + BATCH, len(todo))}/{len(todo)} conditional rules")
    return results


def apply_brand(brand, rules, states, vp, variant, run, prior):
    channels, deck_type = vp["channels"], vp["deck_type"]
    brand_rules = {}
    for e in brand["entries"]:
        if not brand_in_scope(e, channels, variant, deck_type):
            continue
        sev = "block" if e["kind"] == "constraint" else ("warn" if e["kind"] == "override" else "advise")
        brand_rules[e["id"]] = {"id": e["id"], "source": "BRAND", "kind": e["kind"], "status": "active",
                                "severity": sev, "text": e["rule"], "check": e.get("check", ""),
                                "stages": e.get("stages", []), "non_negotiable": e.get("non_negotiable", False),
                                "lint": e.get("lint"), "brand_doc_conflict": e.get("brand_doc_conflict")}
        for t in e.get("targets", []):
            kid = t["kb"]
            if kid not in states:
                continue
            st, kr = states[kid], rules[kid]
            effect = t["effect"]
            st.setdefault("brand", []).append({"id": e["id"], "effect": effect, "brand_rule": e["rule"],
                                               "note": t.get("note", ""), "replacement": t.get("replacement")})
            if st["status"] == "inactive":
                continue  # brand can't modify a rule the profile already switched off
            review = effect in ("override", "tighten", "relax", "kb_retained")
            key = f"activation|rule|{kid}|brand_{effect}|{e['id']}"
            if prior.get(key, {}).get("verdict") == "reject":
                # A reviewer rejected this brand effect earlier. Keep the KB rule as is and record it.
                st["brand"][-1]["suppressed"] = True
                decisions.log(run, stage="activation", variant=variant, source="BRAND", action=f"brand_{effect}",
                              target={"type": "rule", "id": kid}, result="Suppressed by reviewer. KB rule applies unchanged.",
                              rationale=f"{e['source']} (suppressed: reviewer rejected, KB rule applies)",
                              kb_rules=[kid], brand_rules=[e["id"]], kb_default=kr["rule"],
                              review_required=True, prior_reviews=prior)
                continue
            if effect == "override":
                st["status"], st["text"] = "overridden", t["replacement"]
                st["severity"] = max_sev(st["severity"], "warn")
                result = t["replacement"]
            elif effect == "reinforce":
                st["severity"] = max_sev(st["severity"], "block" if e["kind"] == "constraint" else "warn")
                result = f"Severity now {st['severity']}. Brand rule: {e['rule']}"
            elif effect in ("tighten", "relax"):
                st["status"] = "modified"
                st["text"] = f"{kr['rule']} [Brand {e['id']}, {effect}: {t.get('note') or e['rule']}]"
                result = t.get("note") or e["rule"]
            elif effect == "kb_retained":
                result = f"KB rule kept over brand guidance. {t.get('note', '')}"
            else:
                result = e["rule"]
            decisions.log(run, stage="activation", variant=variant,
                          source="KB" if effect == "kb_retained" else "BRAND",
                          action=f"brand_{effect}", target={"type": "rule", "id": kid}, result=result,
                          rationale=f"{e['source']}", kb_rules=[kid], brand_rules=[e["id"]],
                          kb_default=kr["rule"], review_required=review, prior_reviews=prior)
    return brand_rules


def activate(profile_path: str, unclassified: bool = False, run: Path | None = None):
    """run: write into this folder instead of runs/<deck_id> (the plugin's draft folder)."""
    profile = load_json(profile_path)
    kb, rules = load_kb()
    brand = load_brand(ROOT / profile["brand"]) if profile.get("brand") else {"entries": []}
    run = Path(run) if run else run_dir(profile["deck_id"])
    prior = decisions.reviews_by_key(run)
    decisions.clear_stage(run, "activation")
    cache_path = run / "activation_cache.json"
    cache = load_json(cache_path, {})

    out = {"deck_id": profile["deck_id"], "profile": profile, "kb_version": kb["version"],
           "brand": brand.get("brand"), "llm": "none (unclassified)" if unclassified else llm_mode(),
           "variants": {}}
    for v in variants(profile):
        vp = variant_profile(profile, v)
        print(f"{v}: classifying {len(rules)} rules")
        cls = classify(rules, vp, cache, unclassified=unclassified)
        save_json(cache_path, cache)
        states = {}
        for rid, r in rules.items():
            c = cls[rid]
            status = "conditional" if c["status"] == "partial" else c["status"]
            states[rid] = {"id": rid, "source": "KB", "status": status, "reason": c["reason"],
                           "scope_note": c.get("scope_note", ""), "severity": "block" if rid in KB_BLOCKING else SEVERITY[r["strength"]],
                           "strength": r["strength"], "text": r["rule"], "check": r.get("check", ""),
                           "stages": r.get("stages", [])}
            if status == "inactive":
                decisions.log(run, stage="activation", variant=v, source="KB", action="deactivate",
                              target={"type": "rule", "id": rid}, result="inactive", rationale=c["reason"],
                              kb_rules=[rid], prior_reviews=prior)
        brand_rules = apply_brand(brand, rules, states, vp, v, run, prior)
        live = {k: s for k, s in states.items() if s["status"] != "inactive"}
        index = {st: sorted([k for k, s in live.items() if st in s["stages"]] +
                            [k for k, b in brand_rules.items() if st in b["stages"]]) for st in STAGES}
        out["variants"][v] = {"rules": states, "brand_rules": brand_rules, "stage_index": index}

    save_json(run / "active_rules.json", out)
    (run / "activation_report.md").write_text(activation_report(out))
    print(f"wrote {run / 'active_rules.json'} and activation_report.md")
    return out


def activation_report(out) -> str:
    md = [f"# Activation report: {out['deck_id']}", "",
          f"KB v{out['kb_version']}, brand: {out['brand'] or 'none'}, classifier: {out['llm']}.", ""]
    if out["llm"] == "stub":
        md += ["**Classifier was the offline stub. Re-run with the Claude CLI before relying on these results.**", ""]
    elif out["llm"].startswith("none"):
        md += ["**No classifier ran. Conditional rules are kept in force as conditional, so QA may raise findings "
               "a profile-specific activation would have switched off.**", ""]
    for v, data in out["variants"].items():
        st = data["rules"]
        counts = {}
        for s in st.values():
            counts[s["status"]] = counts.get(s["status"], 0) + 1
        md += [f"## Variant: {v}", "", "Rule status: " + ", ".join(f"{k} {n}" for k, n in sorted(counts.items())) +
               f". Brand entries in scope: {len(data['brand_rules'])}.", "",
               "Rules per stage: " + ", ".join(f"{k} {len(ids)}" for k, ids in data["stage_index"].items()) + ".", ""]
        ov = [s for s in st.values() if s.get("brand")]
        if ov:
            md += ["### Brand effects on KB rules", "", "| KB rule | Status | Brand | Effect | Result |", "|---|---|---|---|---|"]
            for s in ov:
                for b in s["brand"]:
                    res = ("SUPPRESSED by reviewer. " if b.get("suppressed") else "") + (b.get("replacement") or b.get("note") or b.get("brand_rule", ""))
                    md.append(f"| {s['id']} | {s['status']} | {b['id']} | {b['effect']} | {res[:140]} |")
            md.append("")
        off = [s for s in st.values() if s["status"] == "inactive"]
        if off:
            md += ["### Switched off for this variant", ""]
            md += [f"- {s['id']}: {s['reason']}" for s in off]
            md.append("")
        cond = [s for s in st.values() if s["status"] == "conditional"]
        if cond:
            md += ["### Partly applicable", ""]
            md += [f"- {s['id']}: {s['scope_note'] or s['reason']}" for s in cond]
            md.append("")
    return "\n".join(md) + "\n"


def rules_for_stage(active, variant: str, stage: str) -> str:
    """Prompt-ready rule block for a pipeline stage. Brand entries are listed with the KB rules they affect."""
    data = active["variants"][variant]
    lines = []
    for rid in data["stage_index"][stage]:
        if rid in data["brand_rules"]:
            b = data["brand_rules"][rid]
            lines.append(f"{rid} [brand {b['kind']}, {b['severity']}] {b['text']}")
        else:
            s = data["rules"][rid]
            tag = {"overridden": " (brand override)", "modified": " (brand-modified)"}.get(s["status"], "")
            lines.append(f"{rid} [{s['severity']}{tag}] {s['text']}" + (f" Scope: {s['scope_note']}" if s["scope_note"] else ""))
    return "\n".join(lines)
