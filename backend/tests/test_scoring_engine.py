"""Unit tests for the pure scoring engine and the registry invariants.

Run from backend/: python -m pytest
(-m puts backend/ on sys.path so `app.*` imports resolve.)
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.scoring.criteria import (
    CONTACT_BANDS,
    CONTACT_FULL_MODEL_SCORE,
    CONTACT_PROPERTIES,
    CONTACT_RULES,
    DEAL_PROPERTIES,
    DEAL_RULES,
    EXCLUDED_PREFIX,
    EXCLUDED_PROPERTIES,
    MQL_LIFECYCLES,
    MQL_THRESHOLD,
    PROBE_COMPANY_PROPERTIES,
    PROBE_CONTACT_PROPERTIES,
    PredicateKind,
    Rule,
    validate_registry,
)
from app.scoring.engine import (
    active_rules,
    band_for,
    classify,
    contact_features_from_api,
    is_active,
    rule_met,
    score_contact_record,
    score_features,
)

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)

ACTIVE_MAX = sum(r.points for r in active_rules())

# Every active rule met: 15+10+10+10+10+5+5+5+10 = 80 (fit registries pending).
COMPLETE_FEATURES = {
    "email": "jane@acme.com",
    "name": "Jane Doe",
    "phone": "+41 22 000 00 00",
    "lifecycle": "customer",
    "company_count": 1,
    "jobtitle": "chief engineer",
    "country": "switzerland",
    "email_domain": "acme.com",
    "source": "ORGANIC_SEARCH",
    "visit_count": 5,
    "last_url": "https://example.org/products/asic",
    "days_since_last_visit": 10,
    "is_unique": True,
}

EMPTY_FEATURES = {
    "email": "",
    "name": "",
    "phone": "",
    "lifecycle": "",
    "company_count": 0,
    "jobtitle": "",
    "country": "",
    "email_domain": "",
    "source": "",
    "visit_count": 0,
    "last_url": "",
    "days_since_last_visit": None,
    "is_unique": None,
}


def test_complete_contact_maxes_active_score():
    result = score_features(COMPLETE_FEATURES)
    assert result["score"] == ACTIVE_MAX == 80
    assert result["max_score"] == ACTIVE_MAX
    assert result["band"] == "hot"
    assert result["classification"] == "mql"
    assert all(c["met"] for c in result["criteria"])


def test_empty_contact_is_cold_nql_with_complete_breakdown():
    result = score_features(EMPTY_FEATURES)
    assert result["score"] == 0
    assert result["band"] == "cold"
    assert result["classification"] == "nql"
    # is_unique is None (unknown corpus) → unique_record is SKIPPED, not
    # scored as a miss: it leaves the breakdown and max_score entirely.
    assert len(result["criteria"]) == len(active_rules()) - 1
    assert "unique_record" not in {c["id"] for c in result["criteria"]}
    assert result["max_score"] == ACTIVE_MAX - 5
    assert all(not c["met"] and c["points"] == 0 for c in result["criteria"])


def test_lifecycle_bonus_matches_mql_set():
    lead = score_features({**EMPTY_FEATURES, "lifecycle": "lead"})
    opportunity = score_features({**EMPTY_FEATURES, "lifecycle": "opportunity"})
    assert not {c["id"]: c for c in lead["criteria"]}["lifecycle_bonus"]["met"]
    assert {c["id"]: c for c in opportunity["criteria"]}["lifecycle_bonus"]["met"]
    assert "opportunity" in MQL_LIFECYCLES and "lead" not in MQL_LIFECYCLES


def test_band_boundary_inclusive():
    # min_points is an inclusive lower bound (>= semantics).
    assert band_for(75) == "hot"
    assert band_for(74) == "warm"
    assert band_for(45) == "warm"
    assert band_for(44) == "cold"
    assert band_for(0) == "cold"
    # A real combo on the warm boundary: name10+company10+lifecycle10+unique5+
    # visited5+engaged5 = 45 (no email — but phone keeps the gates closed).
    result = score_features(
        {
            **EMPTY_FEATURES,
            "name": "Jane",
            "phone": "+41",
            "company_count": 1,
            "lifecycle": "customer",
            "is_unique": True,
            "visit_count": 3,
        }
    )
    assert result["score"] == 45
    assert result["band"] == "warm"
    assert result["classification"] == "mql"  # 45 >= MQL_THRESHOLD


def test_not_in_predicate_requires_truthy_value():
    rule = next(r for r in CONTACT_RULES if r.id == "corporate_email")
    assert rule_met(rule, {"email_domain": "acme.com"})
    assert not rule_met(rule, {"email_domain": "gmail.com"})
    assert not rule_met(rule, {"email_domain": ""})


def test_contains_any_predicate_is_substring_for_urls():
    rule = Rule(id="t", label="t", feature="last_url",
                kind=PredicateKind.CONTAINS_ANY, points=1,
                value=frozenset({"/products", "/pricing"}))
    assert rule_met(rule, {"last_url": "https://ex.org/products/asic"})
    assert not rule_met(rule, {"last_url": "https://ex.org/blog/post"})
    assert not rule_met(rule, {"last_url": ""})


def test_has_any_token_matches_words_not_substrings():
    rule = Rule(id="t", label="t", feature="jobtitle",
                kind=PredicateKind.HAS_ANY_TOKEN, points=1,
                value=frozenset({"engineer", "cto", "vp"}))
    assert rule_met(rule, {"jobtitle": "chief engineer"})
    assert rule_met(rule, {"jobtitle": "CTO & founder".lower()})
    assert rule_met(rule, {"jobtitle": "vp sales"})
    # "cto" is a substring of "director" — token matching must NOT fire.
    assert not rule_met(rule, {"jobtitle": "managing director"})
    assert not rule_met(rule, {"jobtitle": ""})


def test_within_days_predicate():
    rule = next(r for r in CONTACT_RULES if r.id == "recent_visit")
    assert rule_met(rule, {"days_since_last_visit": 0})
    assert rule_met(rule, {"days_since_last_visit": 30})
    assert not rule_met(rule, {"days_since_last_visit": 31})
    assert not rule_met(rule, {"days_since_last_visit": None})


def test_gates_override_points():
    # High-scoring but unreachable → nql, whatever the points say.
    unreachable = {
        **COMPLETE_FEATURES, "email": "", "email_domain": "", "phone": "",
    }
    result = score_features(unreachable)
    assert result["classification"] == "nql"
    # Anonymous (no name) → nql even with email present.
    assert classify({**COMPLETE_FEATURES, "name": ""}, 80) == "nql"
    # Below threshold without gates → borderline.
    assert classify({"email": "a@b.c", "name": "x", "phone": ""}, MQL_THRESHOLD - 1) == "borderline"


def test_unconfigured_rules_are_inactive_and_excluded_from_max():
    role_fit = next(r for r in CONTACT_RULES if r.id == "role_fit")
    assert not is_active(role_fit)  # ROLE_FIT_KEYWORDS pending probe
    assert "role_fit" not in {c["id"] for c in score_features(COMPLETE_FEATURES)["criteria"]}
    configured = Rule(id="role_fit2", label="t", feature="jobtitle",
                      kind=PredicateKind.HAS_ANY_TOKEN, points=8,
                      value=frozenset({"engineer"}))
    assert is_active(configured)
    result = score_features(COMPLETE_FEATURES, rules=(configured,))
    assert result["max_score"] == 8 and result["score"] == 8


def test_contact_features_from_api_record():
    record = {
        "id": "151",
        "properties": {
            "email": " jane@acme.com ",
            "firstname": "Jane",
            "lastname": "",
            "phone": None,
            "lifecyclestage": "Customer",
            "jobtitle": "Chief ENGINEER",
            "country": "",
            "ip_country": "Switzerland",
            "hs_email_domain": "",
            "hs_analytics_source": "ORGANIC_SEARCH",
            "hs_latest_source": "DIRECT_TRAFFIC",
            "hs_analytics_num_visits": "5",
            "hs_analytics_last_url": "https://EX.org/Products",
            "hs_analytics_last_visit_timestamp": (NOW - timedelta(days=10)).isoformat(),
        },
        "associations": {
            "companies": {
                # v3 repeats a company once per association label — de-dupe.
                "results": [
                    {"id": "900", "type": "contact_to_company"},
                    {"id": "900", "type": "contact_to_company_unlabeled"},
                ]
            }
        },
    }
    features = contact_features_from_api(
        record, corpus={"duplicate_ids": {"999"}}, now=NOW
    )
    assert features["email"] == "jane@acme.com"
    assert features["name"] == "Jane"
    assert features["company_count"] == 1
    assert features["lifecycle"] == "customer"
    assert features["jobtitle"] == "chief engineer"
    assert features["country"] == "switzerland"          # ip_country fallback
    assert features["email_domain"] == "acme.com"        # parsed from email
    assert features["source"] == "DIRECT_TRAFFIC"        # latest wins
    assert features["visit_count"] == 5
    assert features["last_url"] == "https://ex.org/products"
    assert features["days_since_last_visit"] == 10
    assert features["is_unique"] is True                 # not in duplicate_ids

    scored = score_contact_record(record, corpus={"duplicate_ids": set()}, now=NOW)
    assert scored["id"] == "151" and scored["score"] == ACTIVE_MAX


def test_extractor_unknowns_stay_unknown():
    features = contact_features_from_api({"id": "1", "properties": {"email": "a@b.c"}})
    assert features["company_count"] == 0
    assert features["days_since_last_visit"] is None  # no `now` injected
    assert features["is_unique"] is None              # no corpus supplied
    # A duplicate id flips is_unique to False.
    dup = contact_features_from_api(
        {"id": "1", "properties": {}}, corpus={"duplicate_ids": {"1"}}
    )
    assert dup["is_unique"] is False


def test_naive_timestamp_is_taken_as_utc_not_a_crash():
    # Offset-less ISO strings must not blow up aware-datetime subtraction.
    record = {
        "id": "1",
        "properties": {"hs_analytics_last_visit_timestamp": "2026-08-21T12:00:00"},
    }
    features = contact_features_from_api(record, now=NOW)
    assert features["days_since_last_visit"] == 10
    # Epoch-milliseconds fallback stays supported.
    epoch = {
        "id": "1",
        "properties": {
            "hs_analytics_last_visit_timestamp": str(
                int((NOW - timedelta(days=3)).timestamp() * 1000)
            )
        },
    }
    assert contact_features_from_api(epoch, now=NOW)["days_since_last_visit"] == 3


def test_registry_invariants():
    ids = [r.id for r in CONTACT_RULES]
    assert len(ids) == len(set(ids)), "rule ids must be unique"
    assert all(r.points > 0 for r in CONTACT_RULES)
    # Documented convention: the FULL model sums to 100 ("score threshold
    # 1-100"). Change deliberately, with this test.
    assert CONTACT_FULL_MODEL_SCORE == 100
    assert 0 < MQL_THRESHOLD <= 100
    mins = [b.min_points for b in CONTACT_BANDS]
    assert mins == sorted(mins, reverse=True) and len(set(mins)) == len(mins)
    assert CONTACT_BANDS[-1].min_points == 0, "last band must catch every score"


def test_binding_rule_no_icalps_inputs():
    # BINDING RULE: the icalps_ namespace never enters the scoring script.
    for props in (CONTACT_PROPERTIES, DEAL_PROPERTIES,
                  PROBE_CONTACT_PROPERTIES, PROBE_COMPANY_PROPERTIES):
        assert not any(p.startswith(EXCLUDED_PREFIX) for p in props)
        assert not set(props) & EXCLUDED_PROPERTIES
    for rule in (*CONTACT_RULES, *DEAL_RULES):
        assert not rule.feature.startswith(EXCLUDED_PREFIX)


def test_validate_registry_raises_on_violation():
    with pytest.raises(ValueError, match="binding rule violation"):
        validate_registry(("email", "icalps_contact_id"), ())
    with pytest.raises(ValueError, match="binding rule violation"):
        validate_registry(("icalps_dealnotes",), ())
    bad_rule = Rule(id="bad", label="bad", feature="icalps_score",
                    kind=PredicateKind.HAS, points=1)
    with pytest.raises(ValueError, match="binding rule violation"):
        validate_registry((), (bad_rule,))
    validate_registry(CONTACT_PROPERTIES, CONTACT_RULES)  # clean set passes
