"""
Pure scoring engine — sync, zero I/O, zero framework imports.

Interprets the declarative registry in app.scoring.criteria against a flat
feature dict. The API layer (routes/scoring.py) owns all HubSpot fetching
and the clock (`now` is injected so time-based rules stay testable); tests
exercise this module with literal dicts.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from app.scoring.criteria import (
    CONTACT_BANDS,
    CONTACT_RULES,
    DISQUALIFIERS,
    MQL_THRESHOLD,
    Band,
    Gate,
    PredicateKind,
    Rule,
)

_SET_KINDS = (
    PredicateKind.IN,
    PredicateKind.NOT_IN,
    PredicateKind.CONTAINS_ANY,
    PredicateKind.HAS_ANY_TOKEN,
)

# Word tokens (>=2 letters so VP/IT/HR survive). Shared with the probe's
# job-title recommendations so what the probe suggests is exactly what
# HAS_ANY_TOKEN matches against.
_TOKEN_RE = re.compile(r"[a-zà-öø-ÿ]{2,}")


def tokenize(text: str) -> frozenset[str]:
    return frozenset(_TOKEN_RE.findall(text.lower()))


def is_active(rule: Rule) -> bool:
    """A membership/keyword rule with an empty set is INACTIVE (pending probe)."""
    if rule.kind in _SET_KINDS:
        return bool(rule.value)
    return True


def active_rules(rules: Sequence[Rule] = CONTACT_RULES) -> tuple[Rule, ...]:
    return tuple(r for r in rules if is_active(r))


def _as_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def rule_met(rule: Rule, features: Mapping[str, object]) -> bool:
    """Evaluate one rule against the extracted features."""
    value = features.get(rule.feature)
    if rule.kind is PredicateKind.HAS:
        return bool(value)
    if rule.kind is PredicateKind.IN:
        return isinstance(rule.value, frozenset) and value in rule.value
    if rule.kind is PredicateKind.NOT_IN:
        return (
            bool(value)
            and isinstance(rule.value, frozenset)
            and value not in rule.value
        )
    if rule.kind is PredicateKind.GTE:
        number = _as_number(value)
        return number is not None and isinstance(rule.value, int) and number >= rule.value
    if rule.kind is PredicateKind.CONTAINS_ANY:
        if not isinstance(value, str) or not value or not isinstance(rule.value, frozenset):
            return False
        return any(keyword in value for keyword in rule.value)
    if rule.kind is PredicateKind.HAS_ANY_TOKEN:
        # Token match, not substring — "cto" must not fire on "director".
        if not isinstance(value, str) or not value or not isinstance(rule.value, frozenset):
            return False
        tokens = tokenize(value)
        return any(keyword in tokens for keyword in rule.value)
    if rule.kind is PredicateKind.WITHIN_DAYS:
        number = _as_number(value)
        return (
            number is not None
            and isinstance(rule.value, int)
            and 0 <= number <= rule.value
        )
    return False


def band_for(score: int, bands: Sequence[Band] = CONTACT_BANDS) -> str:
    """First band (top-down) whose inclusive min_points the score reaches."""
    for band in bands:
        if score >= band.min_points:
            return band.label
    # Unreachable when the registry invariant (last band min_points=0) holds.
    return bands[-1].label


def classify(
    features: Mapping[str, object],
    score: int,
    gates: Sequence[Gate] = DISQUALIFIERS,
    threshold: int = MQL_THRESHOLD,
) -> str:
    """Relevance normalization: gates force 'nql' regardless of points;
    otherwise the threshold splits 'mql' from 'borderline'."""
    for gate in gates:
        if all(not features.get(name) for name in gate.empty_features):
            return "nql"
    return "mql" if score >= threshold else "borderline"


def score_features(
    features: Mapping[str, object],
    rules: Sequence[Rule] = CONTACT_RULES,
    bands: Sequence[Band] = CONTACT_BANDS,
) -> dict:
    """Score a feature dict → integer total, band, classification, breakdown.

    Only ACTIVE rules score and count toward max_score; the breakdown lists
    every active rule in registry order (never sparse). Integer scores and
    lowercase band/classification labels are the write-back contract — do
    not change types casually.
    """
    criteria: list[dict] = []
    score = 0
    max_score = 0
    for rule in active_rules(rules):
        if rule.skip_when_missing and features.get(rule.feature) is None:
            # Unknown tri-state signal (e.g. corpus data absent): the rule is
            # left out of score AND max_score — never scored as a miss.
            continue
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
        "classification": classify(features, score),
        "criteria": criteria,
    }


def _parse_timestamp(raw: str) -> datetime | None:
    """HubSpot v3 datetime property values are ISO-8601 (Z-suffixed) strings;
    epoch-milliseconds are accepted as a fallback. Always returns an aware
    datetime (naive inputs are taken as UTC) so subtraction from an aware
    `now` can never raise."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        pass
    try:
        return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def _parse_int(raw: object) -> int:
    try:
        return int(float(raw))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def contact_features_from_api(
    record: Mapping,
    corpus: Mapping[str, object] | None = None,
    now: datetime | None = None,
) -> dict:
    """Raw CRM v3 contact item {id, properties, associations?} → feature dict.

    Fallback chains: country falls back to ip_country; email_domain to the
    parsed email; source prefers the latest session's source over the
    original. `corpus` carries scan-wide signals (duplicate_ids: set of
    contact ids in any duplicate cluster) — without it, is_unique stays
    None (unknown), never a penalty guess. `now` drives the day-count
    features; without it they stay None (rules unmet).
    """
    props = record.get("properties") or {}
    company_results = (
        (record.get("associations") or {}).get("companies", {}).get("results", [])
    )

    def prop(name: str) -> str:
        return (props.get(name) or "").strip()

    firstname = prop("firstname")
    lastname = prop("lastname")
    email = prop("email")
    email_domain = prop("hs_email_domain").lower()
    if not email_domain and "@" in email:
        email_domain = email.rsplit("@", 1)[1].lower()

    days_since_last_visit: int | None = None
    last_visit = _parse_timestamp(prop("hs_analytics_last_visit_timestamp"))
    if last_visit is not None and now is not None:
        days_since_last_visit = max(0, (now - last_visit).days)

    is_unique: bool | None = None
    if corpus is not None:
        duplicate_ids = corpus.get("duplicate_ids") or frozenset()
        is_unique = str(record.get("id")) not in duplicate_ids

    return {
        "email": email,
        "name": f"{firstname} {lastname}".strip(),
        "phone": prop("phone"),
        "lifecycle": prop("lifecyclestage").lower(),
        "company_count": len({a["id"] for a in company_results}),
        "jobtitle": prop("jobtitle").lower(),
        "country": (prop("country") or prop("ip_country")).lower(),
        "email_domain": email_domain,
        "source": (prop("hs_latest_source") or prop("hs_analytics_source")).upper(),
        "visit_count": _parse_int(props.get("hs_analytics_num_visits")),
        "last_url": prop("hs_analytics_last_url").lower(),
        "days_since_last_visit": days_since_last_visit,
        "is_unique": is_unique,
    }


def score_contact_record(
    record: Mapping,
    corpus: Mapping[str, object] | None = None,
    now: datetime | None = None,
) -> dict:
    """Convenience: raw v3 contact item → {id, score, max_score, band,
    classification, criteria}."""
    return {
        "id": record["id"],
        **score_features(contact_features_from_api(record, corpus=corpus, now=now)),
    }


def rules_as_dicts(rules: Sequence[Rule]) -> list[dict]:
    """JSON-safe registry echo for GET /api/scoring/criteria."""
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
                "configured": is_active(rule),
            }
        )
    return out
