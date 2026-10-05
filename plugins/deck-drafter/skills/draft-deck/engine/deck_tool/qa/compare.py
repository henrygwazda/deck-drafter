"""
Blind comparison of two decks built from the same evidence (PERSUASION_RUN P5). A judge
model, playing the profile's decision-maker, reads both decks with ids stripped and
says which would move it to act. It runs twice, once in each order, with the first
order drawn from a seeded random choice, so position bias shows up as a split verdict
rather than as a false preference. The judge uses a different model from the one that
wrote the decks.
"""
from __future__ import annotations

import json
import random

from ..common import call_json, fill, llm_mode, save_json
from .persuasion import _profile_view, deck_text

JUDGE_MODEL = "opus"

_S = {"type": "string"}
JUDGE_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "deck_1_would_act": {"type": "string", "enum": ["yes", "conditional", "no"]},
    "deck_2_would_act": {"type": "string", "enum": ["yes", "conditional", "no"]},
    "preferred": {"type": "string", "enum": ["1", "2"]},
    "why": _S, "preferred_weaknesses": _S,
    "overstatements": {"type": "array", "items": _S},
    "trust": {"type": "string", "enum": ["1", "2", "equal"]}, "trust_reason": _S,
}, "required": ["deck_1_would_act", "deck_2_would_act", "preferred", "why", "preferred_weaknesses",
                "overstatements", "trust", "trust_reason"]}


def evidence_accounting(narrative: dict, ingested: dict) -> dict:
    """Every ingested evidence item cited by an argument, raised in an unresolved question,
    or excluded with a reason. Returns the ids that are none of these."""
    cited = {e for a in narrative["arguments"] for e in a.get("evidence", [])}
    raised = {e for q in narrative.get("unresolved_questions", []) for e in q.get("evidence", [])}
    excluded = {x["evidence"] for x in narrative.get("excluded_evidence", []) if x.get("reason", "").strip()}
    all_ids = {e["id"] for e in ingested["evidence"]}
    return {"total": len(all_ids), "cited": len(all_ids & cited), "raised_only": len(all_ids & (raised - cited)),
            "excluded": len(all_ids & (excluded - cited - raised)),
            "unaccounted": sorted(all_ids - cited - raised - excluded)}


def _judge_once(profile: dict, first: dict, second: dict) -> dict:
    if llm_mode() == "stub":
        return {"deck_1_would_act": "conditional", "deck_2_would_act": "conditional", "preferred": "1",
                "why": "Stub.", "preferred_weaknesses": "", "overstatements": [], "trust": "equal",
                "trust_reason": "Stub."}
    return call_json(fill("judge_compare.md", PROFILE=_profile_view(profile),
                          DECK_1=deck_text(first, ids=False), DECK_2=deck_text(second, ids=False)),
                     model=JUDGE_MODEL, schema=JUDGE_SCHEMA)


def compare(spec_a: dict, spec_b: dict, profile: dict, labels=("A", "B"), seed: int = 0) -> dict:
    """Judge A against B in both orders. Verdicts are mapped back to the labels."""
    rng = random.Random(seed)
    orders = [(0, 1), (1, 0)]
    rng.shuffle(orders)
    specs = (spec_a, spec_b)
    rounds = []
    for order in orders:
        res = _judge_once(profile, specs[order[0]], specs[order[1]])
        pos = {"1": labels[order[0]], "2": labels[order[1]]}
        rounds.append({
            "order": [labels[i] for i in order],
            "preferred": pos[res["preferred"]],
            "would_act": {pos["1"]: res["deck_1_would_act"], pos["2"]: res["deck_2_would_act"]},
            "trust": pos.get(res["trust"], "equal"),
            "why": res["why"], "preferred_weaknesses": res["preferred_weaknesses"],
            "overstatements": [o.replace("Deck 1:", f"{pos['1']}:").replace("Deck 2:", f"{pos['2']}:")
                               for o in res["overstatements"]],
            "trust_reason": res["trust_reason"], "raw": res,
        })
    prefs = [r["preferred"] for r in rounds]
    return {"labels": list(labels), "seed": seed, "judge_model": JUDGE_MODEL, "rounds": rounds,
            "consistent": prefs[0] == prefs[1], "preferred": prefs[0] if prefs[0] == prefs[1] else "split"}


def report_md(name: str, result: dict, label_names: dict) -> str:
    L = [f"# Blind comparison: {name}", "",
         f"Judge model: {result['judge_model']}. Seed {result['seed']}. Two rounds, one in each order. "
         f"Labels: " + ", ".join(f"{k} = {v}" for k, v in label_names.items()) + ".", "",
         f"**Preferred: {result['preferred']}** ({'same in both orders' if result['consistent'] else 'split between orders'}).", ""]
    for i, r in enumerate(result["rounds"], start=1):
        L += [f"## Round {i}: shown {r['order'][0]} first, {r['order'][1]} second", "",
              f"Preferred {r['preferred']}. Would act: " + ", ".join(f"{k} {v}" for k, v in r["would_act"].items())
              + f". Trusted more: {r['trust']}.", "", f"Why: {r['why']}", "",
              f"Where the preferred deck is weaker: {r['preferred_weaknesses']}", "",
              f"Trust: {r['trust_reason']}", ""]
        if r["overstatements"]:
            L += ["Overstatements noticed:", ""] + [f"- {o}" for o in r["overstatements"]] + [""]
    return "\n".join(L) + "\n"


def write(run_path, name: str, result: dict, label_names: dict):
    save_json(run_path / "compare.json", result)
    (run_path / "compare_report.md").write_text(report_md(name, result, label_names))
    return json.dumps({"preferred": result["preferred"], "consistent": result["consistent"]})
