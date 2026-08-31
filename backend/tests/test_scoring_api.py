"""API-level tests for the scoring routes and the shared contact fetch.

hs_get is monkeypatched with canned paged responses, so these run with no
token and no network; they pin the pagination seam (iter_contact_pages /
iter_company_pages), the corpus duplicate stage, the batch/single/criteria/
probe response shapes, the cache envelope, and the contact-health scan's
behavior through the extracted helper.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.routes.scoring as scoring_routes
from app.cache import health_cache, scoring_cache
from app.config import settings
from app.main import app
from app.routes import hubspot as hubspot_routes

# Captured at import time, before any monkeypatching.
_REAL_HS_GET = hubspot_routes.hs_get

client = TestClient(app)

_RECENT = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()

# Four contacts across two pages. Active rules and points:
# has_email 15, corporate_email 10, has_name 10, has_company 10,
# lifecycle_bonus 10, unique_record 5, visited 5, engaged 5, recent 10 → max 80.
#   c1 complete but email-duplicated with c4 → 75, hot, mql
#   c2 empty (unique) → 5, cold, nql (unreachable + anonymous)
#   c3 name+phone+company, no email → 25, cold, borderline
#   c4 duplicate email, anonymous → 25, cold, nql
_C1 = {
    "id": "1",
    "properties": {
        "email": "jane@acme.com", "firstname": "Jane", "lastname": "Doe",
        "phone": "+41", "lifecyclestage": "customer",
        "jobtitle": "Chief Engineer", "country": "", "ip_country": "Switzerland",
        "hs_latest_source": "DIRECT_TRAFFIC",
        "hs_analytics_num_visits": "5",
        "hs_analytics_last_url": "https://ex.org/products/asic",
        "hs_analytics_last_visit_timestamp": _RECENT,
        "hs_lead_status": "OPEN", "createdate": "2026-01-01T00:00:00Z",
    },
    "associations": {"companies": {"results": [{"id": "900"}]}},
}
_C2 = {
    "id": "2",
    "properties": {
        "email": "", "firstname": "", "lastname": "", "phone": "",
        "lifecyclestage": "lead", "createdate": "2026-01-02T00:00:00Z",
    },
}
_C3 = {
    "id": "3",
    "properties": {
        "email": "", "firstname": "Sam", "lastname": "Roe", "phone": "+33",
        "lifecyclestage": "lead", "jobtitle": "sales representative",
        "createdate": "2026-01-03T00:00:00Z",
    },
    "associations": {"companies": {"results": [{"id": "901"}]}},
}
_C4 = {
    "id": "4",
    "properties": {
        "email": "jane@acme.com", "firstname": "", "lastname": "", "phone": "",
        "lifecyclestage": "lead", "createdate": "2026-01-04T00:00:00Z",
    },
}

_COMPANIES = [
    {"id": "900", "properties": {"domain": "acme.com", "industry": "SEMICONDUCTORS"}},
    {"id": "901", "properties": {"domain": "roe.example", "industry": "SEMICONDUCTORS"}},
    {"id": "902", "properties": {"domain": "", "industry": "BANKING"}},
]


async def _fake_hs_get(path: str, params: dict | None = None) -> dict:
    params = params or {}
    if path == "/crm/v3/objects/contacts":
        if params.get("after") == "p2":
            return {"results": [_C3, _C4]}
        return {"results": [_C1, _C2], "paging": {"next": {"after": "p2"}}}
    if path == "/crm/v3/objects/companies":
        return {"results": _COMPANIES}
    if path == "/crm/v3/objects/contacts/1":
        return _C1
    raise AssertionError(f"unexpected path {path}")


@pytest.fixture(autouse=True)
def _patched(monkeypatch):
    # `from ... import hs_get` copies the reference — patch both bindings.
    monkeypatch.setattr("app.routes.hubspot.hs_get", _fake_hs_get)
    monkeypatch.setattr("app.routes.scoring.hs_get", _fake_hs_get)
    scoring_cache._entries.clear()
    health_cache._entries.clear()
    scoring_routes._last_duplicate_ids = None
    scoring_routes._last_corpus_at = None


def test_criteria_echo():
    body = client.get("/api/scoring/criteria").json()
    assert body["max_score"] == 80          # active rules only
    assert body["full_model_score"] == 100  # once fit registries configured
    assert body["mql_threshold"] == 45
    assert [b["label"] for b in body["bands"]] == ["hot", "warm", "cold"]
    assert {g["id"] for g in body["gates"]} == {"unreachable", "anonymous"}
    by_id = {r["id"]: r for r in body["rules"]}
    assert by_id["corporate_email"]["configured"] is True
    assert by_id["role_fit"]["configured"] is False  # pending probe


def test_batch_scan_corpus_and_classifications():
    cold = client.get("/api/scoring/contacts").json()
    assert cold["scanned"] == 4 and cold["capped"] is False
    assert cold["max_score"] == 80
    assert cold["duplicate_count"] == 2  # c1 + c4 share an email
    assert cold["band_counts"] == {"hot": 1, "warm": 0, "cold": 3}
    assert cold["classification_counts"] == {"mql": 1, "borderline": 1, "nql": 2}
    assert cold["scores"] == [
        {"id": "2", "score": 5, "band": "cold", "classification": "nql"},
        {"id": "3", "score": 25, "band": "cold", "classification": "borderline"},
        {"id": "4", "score": 25, "band": "cold", "classification": "nql"},
        {"id": "1", "score": 75, "band": "hot", "classification": "mql"},
    ]
    assert cold["lowest"][0]["id"] == "2" and cold["highest"][0]["id"] == "1"
    assert cold["average_score"] == round((5 + 25 + 25 + 75) / 4, 1)
    # Bands/gates ride along for the dashboard's dynamic copy.
    assert [b["label"] for b in cold["bands"]] == ["hot", "warm", "cold"]
    assert {g["id"] for g in cold["gates"]} == {"unreachable", "anonymous"}
    # c1 lost exactly the unique_record points to the duplicate cluster.
    c1_row = next(r for r in cold["highest"] if r["id"] == "1")
    assert not next(c for c in c1_row["criteria"] if c["id"] == "unique_record")["met"]
    assert cold["cache"]["cached"] is False

    warm = client.get("/api/scoring/contacts").json()
    assert warm["cache"]["cached"] is True

    refreshed = client.get("/api/scoring/contacts", params={"refresh": "true"}).json()
    assert refreshed["cache"]["cached"] is False


def test_single_contact_score_with_and_without_corpus():
    # Before any batch scan: corpus unknown — unique_record is SKIPPED
    # (out of score AND max), never scored as a miss.
    before = client.get("/api/scoring/contacts/1").json()
    assert before["corpus"] == "unavailable"
    assert before["features"]["is_unique"] is None
    assert before["score"] == 75 and before["max_score"] == 75
    assert "unique_record" not in {c["id"] for c in before["criteria"]}

    # After a batch scan the duplicate set is cached in-process (and c1 is
    # in the email cluster, so the rule is present and unmet).
    client.get("/api/scoring/contacts")
    after = client.get("/api/scoring/contacts/1").json()
    assert after["corpus"] == "cached"
    assert after["features"]["is_unique"] is False
    assert after["score"] == 75 and after["max_score"] == 80
    assert after["classification"] == "mql"

    # An expired corpus is honest again: unavailable, not stale-cached.
    scoring_routes._last_corpus_at = -10**9
    expired = client.get("/api/scoring/contacts/1").json()
    assert expired["corpus"] == "unavailable"


def test_probe_distributions():
    body = client.get("/api/scoring/probe").json()
    assert body["scanned_contacts"] == 4
    assert body["scanned_companies"] == 3
    top = body["top"]
    assert {"value": "engineer", "count": 1} in top["job_title_tokens"]
    assert {"value": "switzerland", "count": 1} in top["countries"]
    assert {"value": "DIRECT_TRAFFIC", "count": 1} in top["sources"]
    assert {"value": "acme.com", "count": 2} in top["email_domains"]
    assert {"value": "/products/asic", "count": 1} in top["url_paths"]
    assert {"value": "SEMICONDUCTORS", "count": 2} in top["industries"]
    assert body["email_domain_split"] == {"freemail": 0, "corporate": 2, "unknown": 2}
    assert body["cache"]["cached"] is False


def test_contact_health_still_works_through_shared_fetch():
    body = client.get("/api/hubspot/contact-health").json()
    assert body["scanned_contacts"] == 4
    assert body["capped"] is False
    assert body["nql_count"] == 2      # c2 (empty) and c4 (anonymous)
    assert body["mql_count"] == 1      # c1: email+name+company
    assert body["borderline_count"] == 1  # c3: reachable but no email
    assert {c["id"] for c in body["nql_contacts"]} == {"2", "4"}
    assert body["duplicate_cluster_count"] == 1  # the shared jane@acme.com email


def test_missing_token_maps_to_500_without_network(monkeypatch):
    # Restore the real hs_get for the single-record path: with an empty
    # token it must fail fast in hs_headers, before any network I/O.
    monkeypatch.setattr("app.routes.scoring.hs_get", _REAL_HS_GET)
    monkeypatch.setattr(settings, "hubspot_token", "")
    resp = client.get("/api/scoring/contacts/1")
    assert resp.status_code == 500
    assert resp.json()["detail"] == "HubSpot token not configured."
