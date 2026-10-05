from __future__ import annotations

from pathlib import Path

from .common import ROOT, load_json

SEVERITY = {"strong": "warn", "moderate": "advise", "single": "advise"}
# KB rules whose violation makes a deck unshippable regardless of profile: claims that
# outrun evidence, unsourced or inaccurate figures, distorted charts, misconceptions as fact.
KB_BLOCKING = {"CLM-06", "CLM-27", "EVD-03", "EVD-31", "EVD-35", "FAIL-05", "FAIL-21", "DAT-22"}
SEV_RANK = {"advise": 0, "warn": 1, "block": 2}


def load_kb(path: str | Path = ROOT / "kb" / "rules_v2.json"):
    kb = load_json(path)
    rules = {}
    for prefix, cat in kb["categories"].items():
        for r in cat["rules"]:
            rules[r["id"]] = dict(r, category=prefix)
    return kb, rules


def resolve(rule_id: str, kb) -> str:
    """Map a retired v1 ID to its canonical v2 ID."""
    return kb.get("aliases", {}).get(rule_id, rule_id)


def load_brand(path: str | Path):
    brand = load_json(path)
    if brand is None:
        raise FileNotFoundError(path)
    return brand


def brand_in_scope(entry, profile_channels: list[str], variant: str, deck_type: str) -> bool:
    scope = entry.get("scope", {})
    chans = scope.get("channels", ["all"])
    if "all" not in chans and not set(chans) & set(profile_channels):
        return False
    modes = scope.get("delivery_modes")
    if modes and variant not in modes:
        return False
    types = scope.get("deck_types")
    if types and deck_type not in types:
        return False
    return True


def max_sev(*sevs: str) -> str:
    return max(sevs, key=lambda s: SEV_RANK[s])
