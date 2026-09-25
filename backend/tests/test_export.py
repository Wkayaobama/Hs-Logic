from app.entities import get_registry


async def test_export_scoped_page_has_labels_and_headers(api):
    r = await api.get("/api/export/deals", params={"entity": "SEALSQ", "limit": 50})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope_mode"] == "search" and body["count"] == 50 and body["next_after"] == "50"
    assert all(rec["properties"]["pipeline"] in ("12096408", "13772279") for rec in body["results"])
    labels = {a["label"] for rec in body["results"] for a in rec["associations"]["companies"]}
    assert "Primary" in labels
    for header in ("X-Logic-Request-Id", "X-Logic-Duration-Ms", "X-Logic-HS-Requests", "X-Logic-HS-429"):
        assert header in r.headers
    # one search + one batch read per association target (companies, contacts)
    assert r.headers["X-Logic-HS-Requests"] == "3"
    assert body["stats"]["hs_requests"] == 3


async def test_export_sample_stops_paging(api):
    r = await api.get("/api/export/contacts", params={"entity": "ICALPS", "sample": 1})
    body = r.json()
    assert body["count"] == 1 and body["next_after"] is None
    assert body["results"][0]["properties"].get("icalps_contactstatus") or body["results"][0]["properties"].get("icalps_contact_id")


async def test_export_no_marker_is_an_empty_flagged_page(api):
    r = await api.get("/api/export/deals", params={"entity": "WECAN"})
    body = r.json()
    assert body["scope_mode"] == "no_marker" and body["no_marker"] is True and body["count"] == 0
    assert body["stats"]["hs_requests"] == 0


async def test_export_complement_excludes_other_entities(api):
    registry = get_registry()
    r = await api.get("/api/export/contacts", params={"entity": "WISEKEY", "limit": 100})
    body = r.json()
    assert body["scope_mode"] == "complement"
    for rec in body["results"]:
        assert registry.classify("contacts", rec["properties"]) == ["WISEKEY"]


async def test_export_unscoped_engagements_and_custom_properties(api):
    r = await api.get("/api/export/notes", params={"entity": "SEALSQ", "limit": 5, "properties": "hs_note_body", "associations": "contacts"})
    body = r.json()
    assert body["scope_mode"] == "unscoped" and body["count"] == 5
    assert "hs_note_body" in body["properties"] and body["associations"] == ["contacts"]


async def test_count_scope_and_reference_filters(api):
    r = await api.get("/api/export/deals/count", params={"entity": "SEALSQ"})
    assert r.json()["total"] == 728 + 303
    r = await api.get("/api/export/deals/count", params={"filter": ["pipeline:IN:12096408|13772279|766126206|705868909"]})
    assert r.json()["total"] == 1820
    r = await api.get("/api/export/deals/count", params={"filter": ["prod___product_name_new_:EQ:QVault TPM IOT"]})
    assert r.json()["total"] == 32
    r = await api.get("/api/export/deals/count", params={"filter": ["prod___product_name_new_:EQ:QVault TPM"]})
    assert r.json()["total"] == 82
    r = await api.get("/api/export/deals/count", params={"filter": ["prod___product_name_new_:NOT_HAS_PROPERTY"]})
    assert r.json()["total"] == 822
    r = await api.get("/api/export/deals/count", params={"entity": "SEALSQ", "filter": ["wisekey___seal:EQ:Wisekey", "pipeline:EQ:12096408"]})
    assert r.json()["method"] == "search" and 0 <= r.json()["total"] <= 22


async def test_count_complement_walks_the_list(api, fake):
    registry = get_registry()
    expected = sum(1 for c in fake.data.records["contacts"] if registry.classify("contacts", c["properties"]) == ["WISEKEY"])
    r = await api.get("/api/export/contacts/count", params={"entity": "WISEKEY"})
    body = r.json()
    assert body["method"] == "list-walk" and body["total"] == expected and not body["capped"]
    assert body["pages_walked"] == 10
    r = await api.get("/api/export/contacts/count", params={"entity": "WISEKEY", "max_pages": 2})
    assert r.json()["capped"] is True and r.json()["pages_walked"] == 2


async def test_associations_endpoint(api, fake):
    ids = ",".join(d["id"] for d in fake.data.records["deals"][:3])
    r = await api.get("/api/export/associations", params={"from": "deals", "to": "companies", "ids": ids})
    assert r.status_code == 200
    assert set(r.json()["results"]) == set(ids.split(","))


async def test_meta_endpoints(api):
    r = await api.get("/api/meta/entities")
    body = r.json()
    assert len(body["entities"]) == 8 and body["entities"][2]["id"] == "SEALSQ"
    assert body["entities"][2]["objects"]["deals"]["mode"] == "search"
    assert "pipeline" in body["marker_properties"]["deals"]
    r = await api.get("/api/meta/properties/deals")
    props = {p["name"]: p for p in r.json()["results"]}
    assert props["prod___product_name_new_"]["type"] == "enumeration"
    assert len(props["prod___product_name_new_"]["options"]) == 35
    assert props["pipeline"]["hubspotDefined"] is True


async def test_unknown_object_and_entity(api):
    assert (await api.get("/api/export/widgets")).status_code == 404
    assert (await api.get("/api/export/deals", params={"entity": "NOPE"})).status_code == 404
    assert (await api.get("/api/export/deals/count", params={"filter": ["pipeline:BOGUS:1"]})).status_code == 400


async def test_existing_portal_route_still_works(api):
    r = await api.get("/api/hubspot/portal")
    assert r.status_code == 200
    assert r.json()["counts"]["deals"] == 1960
