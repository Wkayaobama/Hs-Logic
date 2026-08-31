"""
Pure scoring engine — sync, zero I/O, zero framework imports.

Interprets the declarative registry in app.scoring.criteria against a flat
feature dict. The API layer (routes/scoring.py) owns all HubSpot fetching;
tests exercise this module with literal dicts.
"""

from collections.abc import Mapping, Sequence

from app.scoring.criteria import (
    CONTACT_BANDS,
    CONTACT_RULES,
    Band,
    PredicateKind,
    Rule,
)


def rule_met(rule: Rule, features: Mapping[str, object]) -> bool:
    """Evaluate one rule against the extracted features."""
    value = features.get(rule.feature)
    if rule.kind is PredicateKind.HAS:
        return bool(value)
    if rule.kind is PredicateKind.IN:
        return isinstance(rule.value, frozenset) and value in rule.value
    if rule.kind is PredicateKind.GTE:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        return isinstance(rule.value, int) and value >= rule.value
    return False


def band_for(score: int, bands: Sequence[Band] = CONTACT_BANDS) -> str:
    """First band (top-down) whose inclusive min_points the score reaches."""
    for band in bands:
        if score >= band.min_points:
            return band.label
    # Unreachable when the registry invariant (last band min_points=0) holds.
    return bands[-1].label


def score_features(
    features: Mapping[str, object],
    rules: Sequence[Rule] = CONTACT_RULES,
    bands: Sequence[Band] = CONTACT_BANDS,
) -> dict:
    """Score a feature dict → integer total, band, full per-rule breakdown.

    The breakdown always lists every rule in registry order (never sparse);
    integer scores and lowercase band labels are the Phase 2 write-back
    contract — do not change types casually.
    """
    criteria: list[dict] = []
    score = 0
    max_score = 0
    for rule in rules:
        met = rule_met(rule, features)
        earned = rule.points if met else 0
        score += earned
        max_score += rule.points
        criteria.append(
            {
                "id": rule.id,
                "label": rule.label,
                "met": met,
                "points": earned,
                "max_points": rule.points,
            }
        )
    return {
        "score": score,
        "max_score": max_score,
        "band": band_for(score, bands),
        "criteria": criteria,
    }


def contact_features_from_api(record: Mapping) -> dict:
    """Raw CRM v3 contact item {id, properties, associations?} → feature dict.

    Derivations mirror the contact-health scan: name is first+last collapsed,
    lifecycle is lowercased, company_count de-duplicates the inline
    association rows (v3 repeats a company id once per association label).
    """
    props = record.get("properties") or {}
    company_results = (
        (record.get("associations") or {}).get("companies", {}).get("results", [])
    )
    firstname = (props.get("firstname") or "").strip()
    lastname = (props.get("lastname") or "").strip()
    return {
        "email": (props.get("email") or "").strip(),
        "name": f"{firstname} {lastname}".strip(),
        "phone": (props.get("phone") or "").strip(),
        "lifecycle": (props.get("lifecyclestage") or "").strip().lower(),
        "company_count": len({a["id"] for a in company_results}),
    }


def score_contact_record(record: Mapping) -> dict:
    """Convenience: raw v3 contact item → {id, score, max_score, band, criteria}."""
    return {"id": record["id"], **score_features(contact_features_from_api(record))}


def rules_as_dicts(rules: Sequence[Rule]) -> list[dict]:
    """JSON-safe registry echo for GET /api/scoring/criteria (frozenset → sorted list)."""
    out = []
    for rule in rules:
        value: list[str] | int | None
        if isinstance(rule.value, frozenset):
            value = sorted(rule.value)
        else:
            value = rule.value
        out.append(
            {
                "id": rule.id,
                "label": rule.label,
                "feature": rule.feature,
                "kind": rule.kind.value,
                "points": rule.points,
                "value": value,
            }
        )
    return out
