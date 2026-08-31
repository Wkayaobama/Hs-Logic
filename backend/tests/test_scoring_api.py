"""API-level tests for the scoring routes and the shared contact fetch.

hs_get is monkeypatched with canned paged responses, so these run with no
token and no network; they pin the pagination seam (iter_contact_pages),
the batch/single/criteria response shapes, the cache envelope, and the
contact-health scan's behavior through the extracted helper.
"""

import pytest
from fastapi.testclient import TestClient

from app.cache import health_cache, scoring_cache
from app.config import settings
from app.main import app
from app.routes import hubspot as hubspot_routes

# Captured at import time, before any monkeypatching.
_REAL_HS_GET = hubspot_routes.hs_get

client = TestClient(app)

# Three contacts across two pages: c1 complete (hot/MQL), c2 anonymous and
# unreachable (cold/NQL), c3 name+phone+company but no email (warm/borderline).
_C1 = {
    "id": "1",
    "properties": {
        "email": "jane@acme.com", "firstname": "Jane", "lastname": "Doe",
        "phone": "+41", "lifecyclestage": "customer",
        "hs_lead_status": "OPEN", "createdate": "2026-01-01T00:00:00Z",
    },
    "associations": {"companies": {"results": [{"id": "900"}]}},
}
_C2 = {
    "id": "2",
    "properties": {
        "email": "", "firstname": "", "lastname": "",
        "phone": "", "lifecyclestage": "lead",
        "hs_lead_status": "", "createdate": "2026-01-02T00:00:00Z",
    },
}
_C3 = {
    "id": "3",
    "properties": {
        "email": "", "firstname": "Sam", "lastname": "Roe",
        "phone": "+33", "lifecyclestage": "lead",
        "hs_lead_status": "", "createdate": "2026-01-03T00:00:00Z",
    },
    "associations": {"companies": {"results": [{"id": "901"}]}},
}


async def _fake_hs_get(path: str, params: dict | None = None) -> dict:
    params = params or {}
    if path == "/crm/v3/objects/contacts":
        if params.get("after") == "p2":
            return {"results": [_C3]}
        return {"results": [_C1, _C2], "paging": {"next": {"after": "p2"}}}
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


def test_criteria_echo():
    body = client.get("/api/scoring/criteria").json()
    assert body["max_score"] == 100
    assert [b["label"] for b in body["bands"]] == ["hot", "warm", "cold"]
    assert {r["id"] for r in body["rules"]} == {
        "has_email", "has_name", "has_company", "lifecycle_bonus",
    }


def test_batch_scan_shape_and_cache_envelope():
    cold = client.get("/api/scoring/contacts").json()
    assert cold["scanned"] == 3 and cold["capped"] is False
    assert cold["band_counts"] == {"hot": 1, "warm": 1, "cold": 1}
    assert cold["scores"] == [
        {"id": "2", "score": 0, "band": "cold"},
        {"id": "3", "score": 45, "band": "warm"},
        {"id": "1", "score": 100, "band": "hot"},
    ]
    assert cold["lowest"][0]["id"] == "2" and cold["highest"][0]["id"] == "1"
    assert len(cold["lowest"][0]["criteria"]) == 4
    assert cold["average_score"] == round((0 + 45 + 100) / 3, 1)
    assert cold["cache"]["cached"] is False

    warm = client.get("/api/scoring/contacts").json()
    assert warm["cache"]["cached"] is True

    refreshed = client.get("/api/scoring/contacts", params={"refresh": "true"}).json()
    assert refreshed["cache"]["cached"] is False


def test_single_contact_score():
    body = client.get("/api/scoring/contacts/1").json()
    assert body["id"] == "1" and body["score"] == 100 and body["band"] == "hot"
    assert body["features"]["company_count"] == 1
    assert body["name"] == "Jane Doe"


def test_contact_health_still_works_through_shared_fetch():
    body = client.get("/api/hubspot/contact-health").json()
    assert body["scanned_contacts"] == 3
    assert body["capped"] is False
    assert body["nql_count"] == 1      # c2: unreachable and anonymous
    assert body["mql_count"] == 1      # c1: email+name+company
    assert body["borderline_count"] == 1  # c3: reachable but no email
    assert {c["id"] for c in body["nql_contacts"]} == {"2"}


def test_missing_token_maps_to_500_without_network(monkeypatch):
    # Restore the real hs_get for the single-record path: with an empty
    # token it must fail fast in hs_headers, before any network I/O.
    monkeypatch.setattr("app.routes.scoring.hs_get", _REAL_HS_GET)
    monkeypatch.setattr(settings, "hubspot_token", "")
    resp = client.get("/api/scoring/contacts/1")
    assert resp.status_code == 500
    assert resp.json()["detail"] == "HubSpot token not configured."
