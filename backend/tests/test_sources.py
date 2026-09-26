"""Ad-hoc source module: profile, column matching, record matching, load, fake ingest, routes."""

from pathlib import Path

import pytest

from app.config import get_ruler_dir, get_sampling_dir
from app.sources import load as loader
from app.sources.match import match_columns, schema_proposal
from app.sources.models import SourceSpec
from app.sources.profile import profile_columns, read_rows, sniff

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WECAN = FIXTURES / "wecan_contacts_excerpt.csv"
MIRAEX = FIXTURES / "miraex_drive_deals.csv"


def _profiles(path: Path):
    delimiter, header_row, _ = sniff(path)
    header, rows = read_rows(path, encoding="utf-8-sig", delimiter=delimiter, header_row=header_row)
    return delimiter, header_row, header, rows, profile_columns(header, rows)


def test_profile_skips_title_row_and_infers_types():
    delimiter, header_row, header, rows, profiles = _profiles(WECAN)
    assert delimiter == "," and header_row == 1 and header[0] == "Record ID" and len(rows) == 300
    by = {p.column: p for p in profiles}
    assert by["Email"].inferred_type == "email" and by["Create Date"].inferred_type == "datetime"
    assert by["Marketing contact status"].inferred_type == "enum" and by["Marketing contact status"].values == ["Marketing contact", "Non-marketing contact"]
    assert "excel_mangled_number" in by["Record ID"].flags
    assert by["Primary Associated Company ID"].inferred_type in ("integer", "id")


async def test_match_wecan_contacts_against_target_metadata(hs_client):
    _, _, _, _, profiles = _profiles(WECAN)
    target = await hs_client.get_properties("contacts", stats=__import__("app.hubspot.client", fromlist=["RequestStats"]).RequestStats())
    mappings, derived, notes, keys = match_columns(entity="WECAN", object_type="contacts", profiles=profiles, target_props=target, source_name="wecan")
    m = {x.column: x for x in mappings}
    assert m["First Name"].property == "firstname" and m["First Name"].decision == "auto"
    assert m["Last Name"].property == "lastname" and m["Email"].property == "email" and m["Email"].transform == "lower"
    assert m["Lead Status"].property == "hs_lead_status" and m["Lead Status"].decision in ("auto", "review")
    assert m["Contact owner"].role == "lookup" and m["Contact owner"].match_by == "owner_name"
    assert m["Record ID"].role == "key" and keys == ["Record ID", "Email"]  # composite: ids lost precision in Excel
    assert m["Create Date"].decision == "unmapped" and m["Create Date"].property == "wecan_create_date"  # createdate is read-only
    assert m["Marketing contact status"].proposed_property["type"] == "enumeration"
    assert [d.object for d in derived] == ["companies"]
    d = derived[0]
    assert d.key_column == "Primary Associated Company ID" and d.name_column == "Associated Company"
    assert (d.association_type_id, d.association_reverse_type_id, d.association_label, d.secondary_type_id) == (1, 2, "Primary", 279)
    assert m["Associated Company"].role == "derived"
    assert any("scientific notation" in n for n in notes)
    proposal = schema_proposal("WECAN", "contacts", mappings, derived, "wecan")
    names = {p["name"] for p in proposal["contacts"]}
    assert {"wecan_source_key", "wecan_source_system", "entity_scope", "wecan_create_date"} <= names
    assert {p["name"] for p in proposal["companies"]} >= {"wecan_source_key", "entity_scope"}


async def test_match_miraex_deals_derives_companies(hs_client):
    _, _, _, _, profiles = _profiles(MIRAEX)
    from app.hubspot.client import RequestStats
    target = await hs_client.get_properties("deals", stats=RequestStats())
    mappings, derived, notes, keys = match_columns(entity="MIRAEX", object_type="deals", profiles=profiles, target_props=target, source_name="miraex")
    m = {x.column: x for x in mappings}
    assert m["deal_name"].property == "dealname" and m["deal_name"].decision == "auto"
    assert m["legacy_deal_id"].role == "key" and keys == ["legacy_deal_id"]
    assert m["hs_deal_id"].role == "source_id" and m["hs_deal_id"].decision == "ignored"  # empty here, role kept for future exports
    assert m["hs_dealname"].role == "lookup" and m["hs_dealname"].decision == "ignored"
    assert m["segment"].decision == "unmapped" and m["segment"].proposed_property["name"] == "miraex_segment"
    assert m["drive_created_at"].transform == "datetime" and m["asset_count"].transform == "int"
    assert [d.object for d in derived] == ["companies"] and derived[0].key_column == "legacy_company_id" and derived[0].name_column == "company_name"
    assert derived[0].association_type_id == 5 and derived[0].secondary_type_id == 341
    assert "company_node_key" in derived[0].columns


async def _spec(hs_client, entity, object_type, path):
    from app.hubspot.client import RequestStats
    from app.sources.match import build_spec
    delimiter, header_row, header, rows, profiles = _profiles(path)
    target = await hs_client.get_properties(object_type, stats=RequestStats())
    mappings, derived, notes, keys = match_columns(entity=entity, object_type=object_type, profiles=profiles, target_props=target, source_name=path.stem)
    return build_spec(name=path.stem, entity=entity, object_type=object_type, file=str(path), encoding="utf-8-sig", delimiter=delimiter,
                      header_row=header_row, mappings=mappings, derived=derived, keys=keys, notes=notes, source_system="drive-scan")


async def test_load_miraex_into_probe_surface_and_plan(hs_client, tmp_path):
    spec = await _spec(hs_client, "MIRAEX", "deals", MIRAEX)
    report = await loader.load_source(spec, base_dir=FIXTURES, out_dir=tmp_path / "MIRAEX" / "r1", run_id="r1", match_records=True, client=hs_client)
    assert report["counts"] == {"deals": 6, "companies": 5} and report["skipped_no_key"] == 0
    deals = [__import__("json").loads(l) for l in (tmp_path / "MIRAEX" / "r1" / "deals.jsonl").read_text().splitlines()]
    companies = [__import__("json").loads(l) for l in (tmp_path / "MIRAEX" / "r1" / "companies.jsonl").read_text().splitlines()]
    d = deals[1]
    assert d["properties"]["dealname"].startswith("20260122_PO") and d["properties"]["entity_scope"] == "MIRAEX"
    assert d["properties"]["miraex_source_key"] == "3020QP-f00bc0e0" and d["properties"]["miraex_segment"] == "Quantum"
    assert d["properties"]["miraex_drive_created_at"] == "2026-01-26T14:48:51.000Z"
    assert {a["typeId"] for a in d["associations"]["companies"]} == {5, 341}
    company = next(c for c in companies if c["properties"]["name"] == "Pixel Photonics")
    assert {a["typeId"] for a in company["associations"]["deals"]} == {6, 342} and len(company["associations"]["deals"]) == 4
    assert company["id"].startswith("src-miraex-companies-")
    # deterministic ids
    report2 = await loader.load_source(spec, base_dir=FIXTURES, out_dir=tmp_path / "MIRAEX" / "r2", run_id="r2", match_records=False, client=hs_client)
    deals2 = [__import__("json").loads(l) for l in (tmp_path / "MIRAEX" / "r2" / "deals.jsonl").read_text().splitlines()]
    assert [x["id"] for x in deals2] == [x["id"] for x in deals]
    plan = __import__("json").loads((tmp_path / "MIRAEX" / "r1" / "import_plan.json").read_text())
    assert plan["order"] == ["companies", "deals"] and len(plan["batches"]["deals"]["create"]["inputs"]) == 6
    assert plan["batches"]["deals"]["create"]["endpoint"] == "/crm/v3/objects/deals/batch/create"
    assert {p["name"] for p in plan["properties_to_create"]["deals"]} >= {"miraex_segment", "miraex_source_key", "entity_scope"}
    assert len(plan["associations"]["inputs"]) == 12
    manifest = __import__("json").loads((tmp_path / "MIRAEX" / "r1" / "manifest.json").read_text())
    assert manifest["source"] == "file" and manifest["records"] == 11 and {o["object"] for o in manifest["objects"]} == {"deals", "companies"}


async def test_load_wecan_matches_existing_companies_and_ingests_into_fake(hs_client, fake, tmp_path):
    spec = await _spec(hs_client, "WECAN", "contacts", WECAN)
    # make one company name resolvable against the fake CRM
    target_name = fake.data.records["companies"][0]["properties"]["name"]
    rows_path = tmp_path / "wecan.csv"
    text = WECAN.read_text(encoding="utf-8-sig").splitlines()
    text[2] = text[2].replace("国参特约研究员 Stratesys", target_name)
    rows_path.write_text("\n".join(text) + "\n", encoding="utf-8")
    spec.file = str(rows_path)
    before = fake.counts()
    report = await loader.load_source(spec, base_dir=tmp_path, out_dir=tmp_path / "WECAN" / "r1", run_id="r1", match_records=True, ingest_fake=True, client=hs_client)
    assert report["counts"]["contacts"] + report["duplicate_keys"] + report["skipped_no_key"] == 300 and report["counts"]["contacts"] >= 290
    assert report["counts"]["companies"] > 100
    assert report["matched_existing"].get("companies", 0) >= 1
    assert report["issues"].get("owner needs resolution", 0) > 0
    contacts = [__import__("json").loads(l) for l in (tmp_path / "WECAN" / "r1" / "contacts.jsonl").read_text().splitlines()]
    c = contacts[1]
    assert c["properties"]["email"] == "fumi_jokura@jetro.go.jp" and c["properties"].get("hs_lead_status") is None
    assert c["properties"]["wecan_create_date"] == "2020-09-18T10:57:00.000Z"
    assert {a["typeId"] for a in c["associations"]["companies"]} == {1, 279}
    after = fake.counts()
    assert after["contacts"] == before["contacts"] + report["counts"]["contacts"] and after["companies"] > before["companies"]
    assert report["ingested"]["contacts"]["added"] == report["counts"]["contacts"]
    assert any(p["name"] == "wecan_source_key" for p in fake.properties["contacts"])


async def test_sources_routes_end_to_end(api):
    r = await api.post("/api/sources/profile", json={"file": str(WECAN)})
    assert r.status_code == 200 and r.json()["header_row"] == 1
    r = await api.post("/api/sources/match", json={"entity": "WECAN", "object": "contacts", "file": str(WECAN), "name": "wecan_excerpt", "save": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["derived_objects"] == ["companies"] and body["summary"]["auto"] >= 3
    spec_path = Path(body["saved"]["spec"])
    assert spec_path.exists() and (get_ruler_dir() / "entities" / "WECAN" / "schema.proposed.json").exists()
    r = await api.get("/api/sources/specs", params={"entity": "WECAN"})
    assert r.json()["specs"][0]["name"] == "wecan_excerpt"
    r = await api.post("/api/sources/load", json={"spec": str(spec_path), "run_id": "api1", "match_records": False})
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["contacts"] >= 290
    assert (get_sampling_dir() / "extract" / "WECAN" / "api1" / "contacts.jsonl").exists()
    r = await api.post("/api/sources/profile", json={"file": "/etc/passwd"})
    assert r.status_code == 400


async def test_ingest_refused_against_real_hubspot():
    from app.hubspot.client import HubSpotClient
    client = HubSpotClient("t", "https://api.hubapi.com")
    spec = SourceSpec(name="x", entity="MIRAEX", object="deals", file="x.csv")
    with pytest.raises(RuntimeError):
        await loader.ingest_into_fake(spec, {}, {}, {}, client=client)
