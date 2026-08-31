"""Unit tests for the pure scoring engine and the registry invariants.

Run from backend/: python -m pytest
(-m puts backend/ on sys.path so `app.*` imports resolve.)
"""

from app.scoring.criteria import (
    CONTACT_BANDS,
    CONTACT_MAX_SCORE,
    CONTACT_PROPERTIES,
    CONTACT_RULES,
    DEAL_PROPERTIES,
    DEAL_RULES,
    EXCLUDED_PROPERTIES,
    MQL_LIFECYCLES,
)
from app.scoring.engine import (
    band_for,
    contact_features_from_api,
    score_contact_record,
    score_features,
)

COMPLETE_FEATURES = {
    "email": "jane@acme.com",
    "name": "Jane Doe",
    "phone": "+41 22 000 00 00",
    "lifecycle": "customer",
    "company_count": 1,
}

EMPTY_FEATURES = {
    "email": "",
    "name": "",
    "phone": "",
    "lifecycle": "",
    "company_count": 0,
}


def test_complete_contact_is_hot_100():
    result = score_features(COMPLETE_FEATURES)
    assert result["score"] == 100
    assert result["max_score"] == CONTACT_MAX_SCORE == 100
    assert result["band"] == "hot"
    assert all(c["met"] for c in result["criteria"])


def test_empty_contact_is_cold_zero_with_complete_breakdown():
    result = score_features(EMPTY_FEATURES)
    assert result["score"] == 0
    assert result["band"] == "cold"
    # The breakdown is always complete, never sparse.
    assert len(result["criteria"]) == len(CONTACT_RULES)
    assert all(not c["met"] and c["points"] == 0 for c in result["criteria"])


def test_lifecycle_bonus_matches_mql_set():
    lead = score_features({**EMPTY_FEATURES, "lifecycle": "lead"})
    opportunity = score_features({**EMPTY_FEATURES, "lifecycle": "opportunity"})
    by_id_lead = {c["id"]: c for c in lead["criteria"]}
    by_id_opp = {c["id"]: c for c in opportunity["criteria"]}
    assert not by_id_lead["lifecycle_bonus"]["met"]
    assert by_id_opp["lifecycle_bonus"]["met"]
    assert "opportunity" in MQL_LIFECYCLES and "lead" not in MQL_LIFECYCLES


def test_band_boundary_inclusive():
    # min_points is an inclusive lower bound (>= semantics).
    assert band_for(75) == "hot"
    assert band_for(74) == "warm"
    assert band_for(45) == "warm"
    assert band_for(44) == "cold"
    assert band_for(0) == "cold"
    # A real rule combo landing exactly on the warm boundary: name(20) + company(25).
    result = score_features({**EMPTY_FEATURES, "name": "Jane", "company_count": 1})
    assert result["score"] == 45
    assert result["band"] == "warm"


def test_contact_features_from_api_record():
    record = {
        "id": "151",
        "properties": {
            "email": " jane@acme.com ",
            "firstname": "Jane",
            "lastname": "",
            "phone": None,
            "lifecyclestage": "Customer",
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
    features = contact_features_from_api(record)
    assert features == {
        "email": "jane@acme.com",
        "name": "Jane",
        "phone": "",
        "lifecycle": "customer",
        "company_count": 1,
    }
    scored = score_contact_record(record)
    assert scored["id"] == "151"
    assert scored["score"] == 100  # email + name + company + customer lifecycle


def test_missing_associations_key_is_zero_companies():
    features = contact_features_from_api({"id": "1", "properties": {"email": "a@b.c"}})
    assert features["company_count"] == 0


def test_registry_invariants():
    ids = [r.id for r in CONTACT_RULES]
    assert len(ids) == len(set(ids)), "rule ids must be unique"
    assert all(r.points > 0 for r in CONTACT_RULES)
    # Documented convention: points sum to 100. Change deliberately, with this test.
    assert CONTACT_MAX_SCORE == 100
    mins = [b.min_points for b in CONTACT_BANDS]
    assert mins == sorted(mins, reverse=True) and len(set(mins)) == len(mins)
    assert CONTACT_BANDS[-1].min_points == 0, "last band must catch every score"


def test_registry_excludes_synthetic_ids():
    # Hard exclusion: the synthetic IcAlps ids are provenance, not quality —
    # they must never be fetched nor referenced by any rule.
    assert not set(CONTACT_PROPERTIES) & EXCLUDED_PROPERTIES
    assert not set(DEAL_PROPERTIES) & EXCLUDED_PROPERTIES
    for rule in (*CONTACT_RULES, *DEAL_RULES):
        assert rule.feature not in EXCLUDED_PROPERTIES
