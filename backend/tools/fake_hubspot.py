"""Deterministic fake HubSpot CRM API.

Serves the subset of ``api.hubapi.com`` that the backend uses, seeded from the
reference numbers captured from portal 9201667 on 2026-09-14 (deal counts per
pipeline, product distribution, SEALSQ-scope fill counts) so that the sampling
probe's accuracy stage can be proven end to end without a live token.

Run standalone::

    python -m tools.fake_hubspot --port 8100
    HUBSPOT_BASE_URL=http://127.0.0.1:8100 HUBSPOT_TOKEN=fake uvicorn app.main:app --port 8000

or mount in tests through ``httpx.ASGITransport(app=create_app(FakeHubSpot()))``.

Env knobs (standalone): FAKE_SEED, FAKE_CONTACTS, FAKE_COMPANIES, FAKE_TICKETS,
FAKE_NOTES, FAKE_429_EVERY (inject a 429 every Nth request), FAKE_RETRY_AFTER,
FAKE_SEARCH_CAP (default 10000).
"""

from __future__ import annotations

import argparse
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response

# ── reference constants (2026-09-14 captures) ─────────────────────────────

PIPELINES: list[tuple[str, str]] = [
    ("12096408", "SealSQ Hardware"),
    ("13772279", "SealSQ Services"),
    ("40296576", "Wisekey Services + PKI"),
    ("875072356", "Wise.Sat"),
    ("705868909", "SEALCOIN"),
    ("766126206", "Icalps_hardware"),
    ("934982230", "Quantum & AI"),
]
DEALS_PER_PIPELINE = {
    "12096408": 728, "13772279": 303, "766126206": 788, "705868909": 1,
    "40296576": 140, "875072356": 0, "934982230": 0,
}
SEALSQ_SCOPE = ("12096408", "13772279", "766126206", "705868909")

# (internal value, count) — display labels differ for 'Undefined' (ASIC) and 'QVault 409' (VIC409).
PRODUCT_DISTRIBUTION: list[tuple[str, int]] = [
    ("INES", 240), ("VIC408", 158), ("N/A", 96), ("PKI", 95), ("QVault TPM", 82), ("VIC292", 64),
    ("QS7001", 59), ("Legacy", 34), ("QVault TPM IOT", 32), ("MS6003", 29), ("SCR200", 28),
    ("VIC405 1.2.5", 27), ("VIC155", 25), ("PKI/Other", 21), ("Undefined", 21), ("Wise.Sat", 18),
    ("QVault 409", 16), ("PKI/GSMA", 15), ("MS6001", 14), ("VIC183", 13), ("SCR075", 10),
    ("VIC420", 9), ("NRE", 6), ("VIC405 1.2.6 (CUSTOM - BK Tech)", 5), ("Act2L v1.5 (CISCO)", 4),
    ("SEALBOX", 3), ("VIC186", 3), ("SCR400", 2), ("VIC405 1.2.1 (LEGACY)", 2), ("Wise.Art/NFT", 2),
    ("Act2L v1.3 (CISCO)", 1), ("OCI", 1), ("Quack2 (CISCO)", 1), ("Quack2 HT (CISCO)", 1), ("VIC100", 1),
]  # 1,138 assigned + 822 unassigned = 1,960

SCOPE_FILLS: dict[str, list[tuple[str, int]]] = {
    "sales_region": [("EMEA", 366), ("APAC", 268), ("NORAM", 255), ("TAIWAN", 138)],
    "product_line": [("Legacy", 439), ("PKI", 281), ("Quantum Shield", 189), ("ASIC", 24)],
    "product_hierarchy": [("VIC", 322), ("PKI", 278), ("TPM", 173), ("600x", 50), ("SCR", 42), ("Legacy", 34), ("ASIC", 24), ("CISCO", 7)],
    "tpm_qvault___qs7001___ip__": [("Yes", 161), ("No", 172)],
    "pqc_required": [("YES", 99), ("NO", 44), ("Unknown", 30)],
    "fips_required": [("Yes", 75), ("No", 63), ("Unknown", 35)],
    "military_application": [("Yes", 4), ("No", 223)],
    "vaultitrust_": [("true", 99), ("false", 497)],
    "opp_cust___channel_var__new_": [("Direct", 404), ("Distributor", 246), ("Rep", 60), ("Channel", 33)],
    "revenue_state": [("Pipeline", 450), ("BIBA", 129), ("Forecast", 102)],
    "forecast_source": [("Salesman estimate", 33), ("Customer Conversation", 16), ("Customer Forecast", 5)],
    "quote_status": [("Completed", 27), ("Create", 11)],
    "stratification_of_lost_deals": [("Competition", 89), ("End of Life/Stale Project", 73), ("No Activity", 68), ("Product Issue", 39), ("Unknown", 35), ("Other", 29)],
    "previous_deal_status": [("N/A", 379), ("Identified", 114), ("Qualified", 31), ("Design In", 22), ("Closed Won", 6), ("Closed Lost", 3)],
    "hs_priority": [("medium", 22), ("high", 15), ("low", 7)],
}
ICALPS_FILLS: dict[str, list[tuple[str, int]]] = {
    "icalps_dealstatus": [("Abandonné", 331), ("Won", 328), ("Lost", 60), ("In Progress", 53), ("NoGo", 15)],
    "icalps_stage": [("05 Négociations", 424), ("01 Identification", 144), ("04 Construction propositions", 111), ("02 Qualifiée", 61), ("03 Evaluation technique", 46)],
}
FINAL_CUSTOMER_PER_PIPELINE = {"12096408": 659, "13772279": 270, "705868909": 1, "766126206": 1}
CHANNEL_NAME_PER_PIPELINE = {"12096408": 179, "13772279": 37, "766126206": 1}
DESIGN_WIN_PER_PIPELINE = {"12096408": 84, "40296576": 3}

STAGES: dict[str, list[tuple[str, str, bool]]] = {  # pipeline -> [(stage id, label, closed)]
    "12096408": [("12096409", "Identified", False), ("12096410", "Qualified", False), ("12096411", "Design In", False), ("12096412", "Design Win", False), ("12096869", "Closed Won", True), ("12096415", "Closed Lost", True), ("13772274", "Closed Dead", True)],
    "13772279": [("13772280", "Identified", False), ("13772281", "Qualified", False), ("13772282", "Design In", False), ("31868514", "Design Win", False), ("13772285", "Closed Won", True), ("13772286", "Closed Lost", True), ("13772309", "Closed Dead", True)],
    "40296576": [("85103752", "Identified", False), ("85103753", "Qualified", False), ("85103754", "Design In", False), ("85103755", "Design Win", False), ("85103756", "Closed Won", True), ("85103757", "Closed Dead", True), ("85103758", "Closed lost", True)],
    "875072356": [("1311252059", "Identified", False), ("1311252063", "Closed Won", True)],
    "705868909": [("1031449001", "Identified", False), ("1031449002", "Qualified", False), ("1031449005", "Closed Won", True), ("1031449006", "Closed Lost", True)],
    "766126206": [("1116419644", "Identified", False), ("1116419645", "Qualified", False), ("1116419646", "Design In", False), ("1116419647", "Design Win", False), ("1116419649", "Closed Won", True), ("1116419650", "Closed Dead", True), ("1116652341", "On-Hold", False), ("1313738265", "Closed Lost", True)],
    "934982230": [("1437101470", "Identified", False), ("1437101475", "Closed Won", True)],
}
OWNERS = [("97423250", "78924886"), ("92090369", "78924886"), ("88795766", "78924886"), ("82045380", "70040171"), ("92081232", "3605328"), ("99629073", "2409488"), ("62919224", "2409488")]

# association type pairs: (from_type, to_type, typeId, label, category) with reverse
ASSOC_TYPES: dict[tuple[str, str, int], tuple[str | None, str, int]] = {
    # (from,to,typeId) -> (label, category, reverse typeId)
    ("deals", "companies", 341): (None, "HUBSPOT_DEFINED", 342),
    ("companies", "deals", 342): (None, "HUBSPOT_DEFINED", 341),
    ("deals", "companies", 5): ("Primary", "HUBSPOT_DEFINED", 6),
    ("companies", "deals", 6): ("Primary", "HUBSPOT_DEFINED", 5),
    ("deals", "contacts", 3): (None, "HUBSPOT_DEFINED", 4),
    ("contacts", "deals", 4): (None, "HUBSPOT_DEFINED", 3),
    ("deals", "contacts", 247): ("Champion", "USER_DEFINED", 248),
    ("contacts", "deals", 248): ("Champion", "USER_DEFINED", 247),
    ("contacts", "companies", 279): (None, "HUBSPOT_DEFINED", 280),
    ("companies", "contacts", 280): (None, "HUBSPOT_DEFINED", 279),
    ("contacts", "companies", 1): ("Primary", "HUBSPOT_DEFINED", 2),
    ("companies", "contacts", 2): ("Primary", "HUBSPOT_DEFINED", 1),
    ("contacts", "companies", 271): ("IcAlps_PrimaryContact", "USER_DEFINED", 272),
    ("companies", "contacts", 272): ("IcAlps_PrimaryContact", "USER_DEFINED", 271),
    ("notes", "companies", 190): (None, "HUBSPOT_DEFINED", 189),
    ("companies", "notes", 189): (None, "HUBSPOT_DEFINED", 190),
    ("notes", "contacts", 202): (None, "HUBSPOT_DEFINED", 201),
    ("contacts", "notes", 201): (None, "HUBSPOT_DEFINED", 202),
    ("notes", "deals", 214): (None, "HUBSPOT_DEFINED", 213),
    ("deals", "notes", 213): (None, "HUBSPOT_DEFINED", 214),
    ("tickets", "companies", 339): (None, "HUBSPOT_DEFINED", 340),
    ("companies", "tickets", 340): (None, "HUBSPOT_DEFINED", 339),
    ("tickets", "contacts", 16): (None, "HUBSPOT_DEFINED", 15),
    ("contacts", "tickets", 15): (None, "HUBSPOT_DEFINED", 16),
    ("tickets", "deals", 28): (None, "HUBSPOT_DEFINED", 27),
    ("deals", "tickets", 27): (None, "HUBSPOT_DEFINED", 28),
}
OBJECT_ALIASES = {
    "0-1": "contacts", "0-2": "companies", "0-3": "deals", "0-5": "tickets",
    "0-46": "notes", "0-48": "calls", "0-47": "meetings", "0-27": "tasks",
    "contact": "contacts", "company": "companies", "deal": "deals", "ticket": "tickets",
    "note": "notes", "call": "calls", "meeting": "meetings", "task": "tasks",
}
LASTMOD = {"contacts": "lastmodifieddate"}

BASE_TS = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ── dataset ───────────────────────────────────────────────────────────────


@dataclass
class Dataset:
    records: dict[str, list[dict]] = field(default_factory=dict)          # object -> [record]
    index: dict[str, dict[str, dict]] = field(default_factory=dict)       # object -> id -> record
    assoc: dict[tuple[str, str, str], list[dict]] = field(default_factory=dict)  # (from,to,from_id) -> [{toObjectId,typeId,label,category}]

    def add(self, object_type: str, rec: dict) -> None:
        self.records.setdefault(object_type, []).append(rec)
        self.index.setdefault(object_type, {})[rec["id"]] = rec

    def link(self, from_type: str, from_id: str, to_type: str, to_id: str, type_id: int) -> None:
        label, category, reverse = ASSOC_TYPES[(from_type, to_type, type_id)]
        self.assoc.setdefault((from_type, to_type, from_id), []).append(
            {"toObjectId": to_id, "typeId": type_id, "label": label, "category": category}
        )
        rlabel, rcategory, _ = ASSOC_TYPES[(to_type, from_type, reverse)]
        self.assoc.setdefault((to_type, from_type, to_id), []).append(
            {"toObjectId": from_id, "typeId": reverse, "label": rlabel, "category": rcategory}
        )


def _record(object_type: str, oid: int, props: dict, created: datetime, updated: datetime) -> dict:
    p = dict(props)
    p["hs_object_id"] = str(oid)
    p["createdate"] = _iso(created)
    p[LASTMOD.get(object_type, "hs_lastmodifieddate")] = _iso(updated)
    return {"id": str(oid), "properties": p, "createdAt": _iso(created), "updatedAt": _iso(updated), "archived": False}


def _assign(rng: random.Random, pool: list[dict], prop: str, spec: list[tuple[str, int]]) -> None:
    """Give the first N records of a seeded shuffle of ``pool`` the listed values."""
    order = list(pool)
    rng.shuffle(order)
    i = 0
    for value, count in spec:
        for rec in order[i : i + count]:
            rec["properties"][prop] = value
        i += count


def build_dataset(seed: int = 42, contacts: int = 2500, companies: int = 1200, tickets: int = 200, notes: int = 500) -> Dataset:
    rng = random.Random(seed)
    ds = Dataset()
    next_id = [1001]

    def nid() -> int:
        next_id[0] += 1
        return next_id[0]

    countries = ["CH", "FR", "DE", "US", "TW", "JP", "GB", "SG"]
    industries = ["COMPUTER_HARDWARE", "SEMICONDUCTORS", "TELECOMMUNICATIONS", "DEFENSE_SPACE", "FINANCIAL_SERVICES"]

    # companies
    company_recs: list[dict] = []
    for i in range(companies):
        oid = nid()
        name = f"Company {i:04d} {rng.choice(['SA', 'GmbH', 'Inc', 'Ltd', 'SAS'])}"
        domain = f"company{i:04d}.example.com"
        props: dict[str, Any] = {
            "name": name, "domain": domain, "industry": rng.choice(industries),
            "country": rng.choice(countries), "city": rng.choice(["Geneva", "Grenoble", "Munich", "Taipei", "Boston"]),
            "hubspot_owner_id": rng.choice(OWNERS)[0], "hubspot_team_id": rng.choice(OWNERS)[1],
        }
        created = BASE_TS - timedelta(days=rng.randint(30, 2000))
        updated = created + timedelta(days=rng.randint(0, 29))
        rec = _record("companies", oid, props, created, updated)
        ds.add("companies", rec)
        company_recs.append(rec)
    # marker blocks scale with the dataset size so trimmed test datasets stay valid
    n_icalps = min(400, companies // 3)
    icalps_companies = company_recs[:n_icalps]
    for rec in icalps_companies:
        rec["properties"]["icalps_companytype"] = rng.choice(["Prospect", "Customer", "Supplier", "Agent"])
        rec["properties"]["icalps_companystatus"] = rng.choice(["Active", "Inactive", "Closed", "Archive"])
    for rec in company_recs[n_icalps : n_icalps + 20]:
        rec["properties"]["icalps__sealsq"] = "SEALSQ"
    for rec in company_recs[n_icalps + 20 : n_icalps + 40]:
        rec["properties"]["icalps__sealsq"] = "IC'Alps"
    for rec in company_recs[n_icalps + 40 : n_icalps + 45]:
        rec["properties"]["hs_all_assigned_business_unit_ids"] = "2676052"
    # seeded duplicates in the tail: 15 same-domain pairs, 10 same-name pairs
    tail = company_recs[max(n_icalps + 45, companies - 60):]
    for k in range(min(15, len(tail) // 4)):
        a, b = tail[2 * k], tail[2 * k + 1]
        b["properties"]["domain"] = a["properties"]["domain"]
    name_tail = tail[30:]
    for k in range(min(10, len(name_tail) // 2)):
        a, b = name_tail[2 * k], name_tail[2 * k + 1]
        b["properties"]["name"] = a["properties"]["name"].upper() + " "

    # contacts
    contact_recs: list[dict] = []
    first_names = ["Anna", "Marc", "Lee", "Max", "Ines", "Thomas", "Hue", "Will", "Steve", "Rolf", "Jenna", "Carlos"]
    last_names = ["Sanchez", "Vu", "De Groot", "Clark", "Gobet", "Emch", "Moreno", "Pan", "Luong", "Walton", "Venia", "Hamon"]
    for i in range(contacts):
        oid = nid()
        fn, ln = rng.choice(first_names), rng.choice(last_names)
        props = {
            "email": f"{fn.lower()}.{ln.lower().replace(' ', '')}{i}@example.com",
            "firstname": fn, "lastname": ln, "jobtitle": rng.choice(["CTO", "Buyer", "Engineer", "CEO", None]),
            "lifecyclestage": rng.choice(["lead", "marketingqualifiedlead", "salesqualifiedlead", "customer", None]),
            "hs_lead_status": rng.choice(["NEW", "OPEN", None]),
            "hubspot_owner_id": rng.choice(OWNERS)[0], "hubspot_team_id": rng.choice(OWNERS)[1],
        }
        created = BASE_TS - timedelta(days=rng.randint(1, 1500))
        updated = created + timedelta(days=rng.randint(0, 200))
        rec = _record("contacts", oid, props, created, updated)
        ds.add("contacts", rec)
        contact_recs.append(rec)
    cursor = 0

    def take(n: int) -> list[dict]:
        nonlocal cursor
        out = contact_recs[cursor : cursor + n]
        cursor += n
        return out

    icalps_contacts = take(min(300, contacts // 3))
    for k, rec in enumerate(icalps_contacts):
        rec["properties"]["icalps_contactstatus"] = rng.choice(["true", "false", "active"])
        rec["properties"]["icalps_contact_id"] = f"IC{k:05d}"
    for rec in take(176):
        rec["properties"]["sealsq"] = "true"
    for rec in take(60):
        rec["properties"]["miraex_intent"] = rng.choice(["Careers", "Partnership", "Quantum sensing", "Press / analyst"])
    for rec in take(30):
        rec["properties"]["what_interests_you_about_sealcoin"] = rng.choice(["Token", "Payments", "Satellites"])
    for rec in take(20):
        rec["properties"]["wisekey_department"] = "Wise_Sat"
        rec["properties"]["hs_all_assigned_business_unit_ids"] = "2676052"
    for rec in take(40):
        rec["properties"]["wisekey_department"] = rng.choice(["Finance", "Wise_Art", "Public relations"])
    # name duplicates (20 pairs) among plain contacts
    plain = contact_recs[cursor:]
    for k in range(min(20, len(plain) // 2)):
        a, b = plain[2 * k], plain[2 * k + 1]
        b["properties"]["firstname"], b["properties"]["lastname"] = a["properties"]["firstname"], a["properties"]["lastname"]
        b["properties"]["company"] = a["properties"].get("company")

    # contact -> company associations (279 default + 1 Primary); seeded violations
    for k, rec in enumerate(contact_recs):
        roll = rng.random()
        if roll < 0.02:
            continue  # orphan contact
        comp = rng.choice(company_recs)
        rec["properties"]["company"] = comp["properties"]["name"]
        ds.link("contacts", rec["id"], "companies", comp["id"], 279)
        ds.link("contacts", rec["id"], "companies", comp["id"], 1)
        if roll < 0.05:  # two Primary companies -> cardinality violation
            other = rng.choice(company_recs)
            if other["id"] != comp["id"]:
                ds.link("contacts", rec["id"], "companies", other["id"], 279)
                ds.link("contacts", rec["id"], "companies", other["id"], 1)
    # IcAlps primary contact per IC'Alps company (1:1) for 84%, 3 companies with two
    for k, comp in enumerate(icalps_companies):
        if rng.random() < 0.84 and icalps_contacts:
            c = rng.choice(icalps_contacts)
            ds.link("companies", comp["id"], "contacts", c["id"], 272)
            if k < 3:
                c2 = rng.choice(icalps_contacts)
                if c2["id"] != c["id"]:
                    ds.link("companies", comp["id"], "contacts", c2["id"], 272)

    # deals
    deal_recs: list[dict] = []
    by_pipeline: dict[str, list[dict]] = {}
    for pipeline, count in DEALS_PER_PIPELINE.items():
        for i in range(count):
            oid = nid()
            stage_id, _, closed = rng.choice(STAGES[pipeline])
            won = closed and "Won" in dict((s[0], s[1]) for s in STAGES[pipeline])[stage_id]
            amount = round(rng.uniform(1, 500) * 1000, 2)
            props = {
                "dealname": f"Deal {pipeline}-{i:04d}", "amount": str(amount),
                "amount_in_home_currency": str(amount), "pipeline": pipeline, "dealstage": stage_id,
                "hs_is_closed": "true" if closed else "false", "hs_is_closed_won": "true" if won else "false",
                "closedate": _iso(BASE_TS + timedelta(days=rng.randint(-400, 400))),
                "hubspot_owner_id": rng.choice(OWNERS)[0], "hubspot_team_id": rng.choice(OWNERS)[1],
                "notes_last_updated": _iso(BASE_TS - timedelta(days=rng.randint(0, 400))),
                "deal_currency_code": "EUR" if pipeline == "766126206" else "USD",
                "wisekey___seal": "Wisekey" if pipeline == "40296576" else "Seal",
            }
            created = BASE_TS - timedelta(days=rng.randint(10, 1800))
            updated = created + timedelta(days=rng.randint(0, 9))
            rec = _record("deals", oid, props, created, updated)
            ds.add("deals", rec)
            deal_recs.append(rec)
            by_pipeline.setdefault(pipeline, []).append(rec)
    scope_deals = [r for r in deal_recs if r["properties"]["pipeline"] in SEALSQ_SCOPE]
    _assign(rng, deal_recs, "prod___product_name_new_", PRODUCT_DISTRIBUTION)
    for prop, spec in SCOPE_FILLS.items():
        _assign(rng, scope_deals, prop, spec)
    _assign(rng, scope_deals, "wisekey___seal", [("Wisekey", 22)])  # 1,798 Seal + 22 Wisekey in scope
    icalps_deals = by_pipeline.get("766126206", [])
    for prop, spec in ICALPS_FILLS.items():
        _assign(rng, icalps_deals, prop, spec)
    _assign(rng, icalps_deals, "deal_currency_code", [("USD", 1)])  # USD 1,033 / EUR 787
    for pipeline, count in FINAL_CUSTOMER_PER_PIPELINE.items():
        _assign(rng, by_pipeline.get(pipeline, []), "final_customer", [(f"Customer {k}", 1) for k in range(count)])
    for pipeline, count in CHANNEL_NAME_PER_PIPELINE.items():
        _assign(rng, by_pipeline.get(pipeline, []), "channel_name__dist_rep_", [(rng.choice(["Arrow", "Avnet", "Mouser", "Digikey"]), 1) for _ in range(count)])
    for pipeline, count in DESIGN_WIN_PER_PIPELINE.items():
        _assign(rng, by_pipeline.get(pipeline, []), "design_win_date", [(_iso(BASE_TS - timedelta(days=rng.randint(0, 900)))[:10], 1) for _ in range(count)])

    # deal -> company (341 default + 5 Primary) for 99.7%; 5 deals with two Primary
    for k, rec in enumerate(deal_recs):
        if rng.random() < 0.003:
            continue
        comp = rng.choice(company_recs)
        ds.link("deals", rec["id"], "companies", comp["id"], 341)
        ds.link("deals", rec["id"], "companies", comp["id"], 5)
        if k < 5:
            other = rng.choice(company_recs)
            if other["id"] != comp["id"]:
                ds.link("deals", rec["id"], "companies", other["id"], 341)
                ds.link("deals", rec["id"], "companies", other["id"], 5)
        # deal -> contact (3 default, 247 Champion) for 82.5%
        if rng.random() < 0.825:
            c = rng.choice(contact_recs)
            ds.link("deals", rec["id"], "contacts", c["id"], 3)
            if rng.random() < 0.3:
                ds.link("deals", rec["id"], "contacts", c["id"], 247)

    # tickets
    ticket_recs: list[dict] = []
    for i in range(tickets):
        oid = nid()
        icalps = i < int(tickets * 0.6)
        props = {
            "subject": f"Ticket {i:04d}", "hs_pipeline": "0" if i % 4 else "820219545",
            "hs_pipeline_stage": rng.choice(["1", "2", "3", "4"]), "hs_ticket_priority": rng.choice(["LOW", "MEDIUM", "HIGH"]),
            "hubspot_owner_id": rng.choice(OWNERS)[0], "is_icalps": "true" if icalps else "false",
        }
        if icalps:
            props["icalps_ticketcasetype"] = rng.choice(["Nsat", "Satisfaction", "Rec", "TBD"])
        created = BASE_TS - timedelta(days=rng.randint(1, 900))
        rec = _record("tickets", oid, props, created, created + timedelta(days=rng.randint(0, 5)))
        ds.add("tickets", rec)
        ticket_recs.append(rec)
        if rng.random() < 0.9:
            ds.link("tickets", rec["id"], "companies", rng.choice(company_recs)["id"], 339)
        if rng.random() < 0.8:
            ds.link("tickets", rec["id"], "contacts", rng.choice(contact_recs)["id"], 16)

    # notes
    for i in range(notes):
        oid = nid()
        ts = BASE_TS - timedelta(days=rng.randint(0, 700))
        props = {"hs_timestamp": _iso(ts), "hubspot_owner_id": rng.choice(OWNERS)[0], "hs_note_body": f"Note {i} body"}
        rec = _record("notes", oid, props, ts, ts)
        ds.add("notes", rec)
        if rng.random() < 0.02:
            continue  # orphan note
        if rng.random() < 0.6:
            ds.link("notes", rec["id"], "contacts", rng.choice(contact_recs)["id"], 202)
        if rng.random() < 0.5:
            ds.link("notes", rec["id"], "companies", rng.choice(company_recs)["id"], 190)
        if rng.random() < 0.2:
            ds.link("notes", rec["id"], "deals", rng.choice(deal_recs)["id"], 214)
    for empty in ("calls", "meetings", "tasks"):
        ds.records.setdefault(empty, [])
        ds.index.setdefault(empty, {})
    return ds


# ── property definitions served by /crm/v3/properties ─────────────────────


def _enum(name: str, label: str, values: list[str], group: str = "dealinformation", hs: bool = False) -> dict:
    return {
        "name": name, "label": label, "type": "enumeration", "fieldType": "select", "groupName": group,
        "hubspotDefined": hs, "calculated": False, "hidden": False,
        "options": [{"value": v, "label": v, "hidden": False, "displayOrder": i} for i, v in enumerate(values)],
    }


def _plain(name: str, label: str, type_: str = "string", field_type: str = "text", group: str = "dealinformation", hs: bool = True) -> dict:
    return {"name": name, "label": label, "type": type_, "fieldType": field_type, "groupName": group,
            "hubspotDefined": hs, "calculated": False, "hidden": False, "options": []}


def property_definitions() -> dict[str, list[dict]]:
    deals = [
        _plain("dealname", "Deal Name"), _plain("amount", "Amount", "number", "number"),
        _plain("amount_in_home_currency", "Amount in company currency", "number", "number"),
        _enum("pipeline", "Pipeline", [p for p, _ in PIPELINES], hs=True),
        _enum("dealstage", "Deal Stage", [s[0] for stages in STAGES.values() for s in stages], hs=True),
        _plain("closedate", "Close Date", "datetime", "date"), _plain("createdate", "Create Date", "datetime", "date"),
        _plain("hs_lastmodifieddate", "Last Modified Date", "datetime", "date"),
        _enum("hs_is_closed", "Is Deal Closed?", ["true", "false"], hs=True),
        _enum("hs_is_closed_won", "Is Closed Won", ["true", "false"], hs=True),
        _enum("prod___product_name_new_", "PROD - Product name(new)", [v for v, _ in PRODUCT_DISTRIBUTION]),
        _enum("wisekey___seal", "Wisekey / Seal", ["Seal", "Wisekey"]),
        _enum("icalps__sealsq", "IC'Alps | SEALSQ", ["SEALSQ", "IC'Alps"]),
        _enum("deal_currency_code", "Currency", ["USD", "EUR"], hs=True),
        _enum("hs_all_assigned_business_unit_ids", "Brands", ["0", "2676052"], hs=True),
        _plain("final_customer", "OPP CUST - Final Customer"), _plain("channel_name__dist_rep_", "Channel name [Dist/Rep]"),
        _plain("design_win_date", "DATE - 4. Design Win (Hardware only)", "date", "date", hs=False),
        _plain("notes_last_updated", "Last Activity Date", "datetime", "date"),
        _plain("hubspot_owner_id", "Deal owner", "enumeration", "select"), _plain("hubspot_team_id", "HubSpot Team", "enumeration", "select"),
    ]
    for prop, spec in {**SCOPE_FILLS, **ICALPS_FILLS}.items():
        deals.append(_enum(prop, prop, [v for v, _ in spec]))
    contacts = [
        _plain("email", "Email"), _plain("firstname", "First Name"), _plain("lastname", "Last Name"),
        _plain("company", "Company Name"), _plain("jobtitle", "Job Title"),
        _enum("lifecyclestage", "Lifecycle Stage", ["subscriber", "lead", "marketingqualifiedlead", "salesqualifiedlead", "opportunity", "customer", "evangelist", "other"], "contactinformation", True),
        _enum("hs_lead_status", "Lead Status", ["NEW", "OPEN", "IN_PROGRESS", "OPEN_DEAL", "UNQUALIFIED", "ATTEMPTED_TO_CONTACT", "CONNECTED", "BAD_TIMING"], "contactinformation", True),
        _plain("createdate", "Create Date", "datetime", "date", "contactinformation"),
        _plain("lastmodifieddate", "Last Modified Date", "datetime", "date", "contactinformation"),
        _enum("icalps_contactstatus", "icalps_contactstatus", ["true", "false", "active", "inactive"], "contactinformation"),
        _plain("icalps_contact_id", "icalps_contact_id", group="contactinformation", hs=False),
        _enum("sealsq", "SEALSQ", ["true", "false"], "contactinformation"),
        _plain("sealsq_enquiry_type", "SEALSQ enquiry type", group="contactinformation", hs=False),
        _enum("miraex_intent", "How can we help?", ["OEM design / evaluation (under NDA)", "Exclusive licensing", "Distributed quantum computing", "Quantum sensing", "Quantum networking / QKD", "Partnership", "Press / analyst", "Careers"], "contactinformation"),
        _plain("what_interests_you_about_sealcoin", "What interests you about SEALCOIN", group="contactinformation", hs=False),
        _plain("how_did_you_hear_about_sealcoin_token", "How did you hear about SEALCOIN Token", group="contactinformation", hs=False),
        _enum("wisekey_department", "Wisekey_Department", ["Finance", "Wise_Art", "Wise_Sat", "Public relations"], "contactinformation"),
        _enum("hs_all_assigned_business_unit_ids", "Brands", ["0", "2676052"], "contactinformation", True),
        _plain("hubspot_owner_id", "Contact owner", "enumeration", "select", "contactinformation"),
        _plain("hubspot_team_id", "HubSpot Team", "enumeration", "select", "contactinformation"),
    ]
    companies = [
        _plain("name", "Company name", group="companyinformation"), _plain("domain", "Company Domain Name", group="companyinformation"),
        _enum("industry", "Industry", ["COMPUTER_HARDWARE", "SEMICONDUCTORS", "TELECOMMUNICATIONS", "DEFENSE_SPACE", "FINANCIAL_SERVICES"], "companyinformation", True),
        _plain("country", "Country/Region", group="companyinformation"), _plain("city", "City", group="companyinformation"),
        _plain("createdate", "Create Date", "datetime", "date", "companyinformation"),
        _plain("hs_lastmodifieddate", "Last Modified Date", "datetime", "date", "companyinformation"),
        _enum("icalps_companytype", "IcAlps_CompanyType", ["Prospect", "Supplier", "Customer", "Agent"], "companyinformation"),
        _enum("icalps_companystatus", "IcAlps_CompanyStatus", ["Active", "Inactive", "Closed", "Archive"], "companyinformation"),
        _enum("icalps__sealsq", "IC'Alps | SEALSQ", ["SEALSQ", "IC'Alps"], "companyinformation"),
        _enum("hs_all_assigned_business_unit_ids", "Brands", ["0", "2676052"], "companyinformation", True),
        _plain("hubspot_owner_id", "Company owner", "enumeration", "select", "companyinformation"),
        _plain("hubspot_team_id", "HubSpot Team", "enumeration", "select", "companyinformation"),
    ]
    tickets = [
        _plain("subject", "Ticket name", group="ticketinformation"),
        _enum("hs_pipeline", "Pipeline", ["0", "820219545"], "ticketinformation", True),
        _enum("hs_pipeline_stage", "Ticket status", ["1", "2", "3", "4"], "ticketinformation", True),
        _enum("hs_ticket_priority", "Priority", ["LOW", "MEDIUM", "HIGH"], "ticketinformation", True),
        _enum("is_icalps", "Is_IcAlps ?", ["true", "false"], "ticketinformation"),
        _enum("icalps_ticketcasetype", "IcAlps_Ticketcasetype", ["Nsat", "Satisfaction", "Rec", "TBD"], "ticketinformation"),
        _plain("createdate", "Create date", "datetime", "date", "ticketinformation"),
        _plain("hs_lastmodifieddate", "Last modified date", "datetime", "date", "ticketinformation"),
        _plain("hubspot_owner_id", "Ticket owner", "enumeration", "select", "ticketinformation"),
        _enum("hs_all_assigned_business_unit_ids", "Brands", ["0", "2676052"], "ticketinformation", True),
    ]
    engagement = [
        _plain("hs_timestamp", "Activity date", "datetime", "date", "engagement"),
        _plain("hs_lastmodifieddate", "Last modified date", "datetime", "date", "engagement"),
        _plain("hubspot_owner_id", "Activity assigned to", "enumeration", "select", "engagement"),
    ]
    return {
        "contacts": contacts, "companies": companies, "deals": deals, "tickets": tickets,
        "notes": engagement + [_plain("hs_note_body", "Note body", group="engagement")],
        "calls": engagement + [_plain("hs_call_disposition", "Call outcome", "enumeration", "select", "engagement")],
        "meetings": engagement + [_plain("hs_meeting_outcome", "Meeting outcome", "enumeration", "select", "engagement")],
        "tasks": engagement + [_plain("hs_task_status", "Task status", "enumeration", "select", "engagement"), _plain("hs_task_type", "Task type", "enumeration", "select", "engagement")],
    }


# ── the fake server ───────────────────────────────────────────────────────


class FakeHubSpot:
    def __init__(self, seed: int = 42, *, contacts: int = 2500, companies: int = 1200, tickets: int = 200, notes: int = 500) -> None:
        self.data = build_dataset(seed, contacts=contacts, companies=companies, tickets=tickets, notes=notes)
        self.properties = property_definitions()
        self.requests = 0
        self.inject_429_every = 0
        self.retry_after = "0"
        self.search_cap = 10_000
        self.log: list[str] = []

    @classmethod
    def from_env(cls) -> "FakeHubSpot":
        fake = cls(
            int(os.environ.get("FAKE_SEED", "42")),
            contacts=int(os.environ.get("FAKE_CONTACTS", "2500")),
            companies=int(os.environ.get("FAKE_COMPANIES", "1200")),
            tickets=int(os.environ.get("FAKE_TICKETS", "200")),
            notes=int(os.environ.get("FAKE_NOTES", "500")),
        )
        fake.inject_429_every = int(os.environ.get("FAKE_429_EVERY", "0"))
        fake.retry_after = os.environ.get("FAKE_RETRY_AFTER", "0")
        fake.search_cap = int(os.environ.get("FAKE_SEARCH_CAP", "10000"))
        return fake

    def counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.data.records.items()}


def _object(name: str) -> str:
    key = OBJECT_ALIASES.get(name, name)
    if key not in ("contacts", "companies", "deals", "tickets", "notes", "calls", "meetings", "tasks"):
        raise HTTPException(status_code=404, detail={"message": f"Unable to infer object type from: {name}"})
    return key


def _project(object_type: str, rec: dict, properties: list[str] | None) -> dict:
    p = rec["properties"]
    always = ["hs_object_id", "createdate", LASTMOD.get(object_type, "hs_lastmodifieddate")]
    wanted = list(dict.fromkeys((properties or []) + always))
    known = {d["name"] for d in property_definitions().get(object_type, [])} | set(p.keys())
    out = {name: p.get(name) for name in wanted if name in known}
    return {"id": rec["id"], "properties": out, "createdAt": rec["createdAt"], "updatedAt": rec["updatedAt"], "archived": rec["archived"]}


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _match(rec: dict, f: dict) -> bool:
    raw = rec["properties"].get(f.get("propertyName"))
    present = raw is not None and str(raw) != ""
    op = f.get("operator")
    tokens = [t for t in str(raw).split(";") if t] if present else []
    if op == "HAS_PROPERTY":
        return present
    if op == "NOT_HAS_PROPERTY":
        return not present
    value = f.get("value")
    if op == "EQ":
        return present and (str(raw) == str(value) or str(value) in tokens)
    if op == "NEQ":
        return not (present and (str(raw) == str(value) or str(value) in tokens))
    if op in ("IN", "NOT_IN"):
        wanted = {str(v) for v in f.get("values", [])}
        hit = present and (str(raw) in wanted or any(t in wanted for t in tokens))
        return hit if op == "IN" else not hit
    if op == "CONTAINS_TOKEN":
        return present and str(value).lower().strip("*") in str(raw).lower()
    if op == "NOT_CONTAINS_TOKEN":
        return not (present and str(value).lower().strip("*") in str(raw).lower())
    if op in ("GT", "GTE", "LT", "LTE"):
        if not present:
            return False
        a, b = _num(raw), _num(value)
        left, right = (a, b) if a is not None and b is not None else (str(raw), str(value))
        return {"GT": left > right, "GTE": left >= right, "LT": left < right, "LTE": left <= right}[op]
    if op == "BETWEEN":
        if not present:
            return False
        a, lo, hi = _num(raw), _num(value), _num(f.get("highValue"))
        if a is None or lo is None or hi is None:
            return str(value) <= str(raw) <= str(f.get("highValue"))
        return lo <= a <= hi
    raise HTTPException(status_code=400, detail={"message": f"Unsupported operator {op}"})


def create_app(fake: FakeHubSpot) -> FastAPI:
    api = FastAPI(title="fake-hubspot", docs_url=None, redoc_url=None, openapi_url=None)
    api.state.fake = fake

    @api.middleware("http")
    async def gate(request: Request, call_next):
        fake.requests += 1
        fake.log.append(f"{request.method} {request.url.path}")
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or len(auth) <= 7:
            return Response('{"status":"error","message":"Authentication credentials not found."}', status_code=401, media_type="application/json")
        if auth == "Bearer expired":
            return Response('{"status":"error","message":"The access token is expired."}', status_code=401, media_type="application/json")
        if auth == "Bearer noscope":
            return Response('{"status":"error","message":"This app hasn\'t been granted all required scopes."}', status_code=403, media_type="application/json")
        if fake.inject_429_every and fake.requests % fake.inject_429_every == 0:
            return Response('{"status":"error","message":"You have reached your ten_secondly_rolling limit."}', status_code=429, media_type="application/json", headers={"Retry-After": fake.retry_after})
        return await call_next(request)

    @api.get("/account-info/v3/details")
    async def account():
        return {"portalId": 9201667, "companyName": "WISeKey SA (fake)", "timeZone": "Europe/Berlin",
                "defaultHubspotCurrency": "USD", "createdAt": "2021-01-14T08:34:35Z", "hubDomain": "fake.hubspot.com", "accountType": "STANDARD"}

    @api.get("/crm/v3/pipelines/deals")
    async def pipelines():
        return {"results": [
            {"id": pid, "label": label, "displayOrder": i,
             "stages": [{"id": s[0], "label": s[1], "displayOrder": j, "metadata": {"probability": "1.0" if s[2] and "Won" in s[1] else "0.0" if s[2] else "0.2", "isClosed": str(s[2]).lower()}} for j, s in enumerate(STAGES[pid])]}
            for i, (pid, label) in enumerate(PIPELINES)
        ]}

    @api.get("/crm/v3/properties/{object_type}")
    async def properties(object_type: str):
        key = _object(object_type)
        return {"results": fake.properties.get(key, [])}

    @api.get("/crm/v3/objects/{object_type}")
    async def list_objects(object_type: str, limit: int = 10, after: str | None = None, properties: str | None = None, associations: str | None = None):
        key = _object(object_type)
        limit = max(1, min(limit, 100))
        try:
            offset = int(after) if after else 0
        except ValueError:
            raise HTTPException(status_code=400, detail={"message": "Invalid after cursor"})
        rows = fake.data.records.get(key, [])
        page = rows[offset : offset + limit]
        props = [p for p in (properties or "").split(",") if p]
        results = []
        for rec in page:
            item = _project(key, rec, props)
            if associations:
                item["associations"] = {}
                for to in [a for a in associations.split(",") if a]:
                    to_key = _object(to)
                    links = fake.data.assoc.get((key, to_key, rec["id"]), [])
                    if links:
                        item["associations"][to_key] = {"results": [{"id": l["toObjectId"], "type": f"{key[:-1]}_to_{to_key[:-1]}"} for l in links]}
            results.append(item)
        body: dict[str, Any] = {"results": results}
        if offset + limit < len(rows):
            body["paging"] = {"next": {"after": str(offset + limit), "link": f"?after={offset + limit}"}}
        return body

    @api.post("/crm/v3/objects/{object_type}/search")
    async def search(object_type: str, request: Request):
        key = _object(object_type)
        body = await request.json()
        limit = max(1, min(int(body.get("limit", 10)), 200))
        after = body.get("after")
        try:
            offset = int(after) if after not in (None, "") else 0
        except ValueError:
            raise HTTPException(status_code=400, detail={"message": "Invalid after cursor"})
        groups = body.get("filterGroups") or []
        if len(groups) > 6 or any(len(g.get("filters", [])) > 6 for g in groups):
            raise HTTPException(status_code=400, detail={"message": "Too many filterGroups/filters"})
        rows = fake.data.records.get(key, [])
        if groups:
            rows = [r for r in rows if any(all(_match(r, f) for f in g.get("filters", [])) for g in groups)]
        total = len(rows)
        page = rows[offset : offset + limit]
        props = body.get("properties") or []
        out: dict[str, Any] = {"total": total, "results": [_project(key, r, props) for r in page]}
        nxt = offset + limit
        if nxt < total and nxt < fake.search_cap:
            out["paging"] = {"next": {"after": str(nxt)}}
        return out

    @api.post("/crm/v4/associations/{from_type}/{to_type}/batch/read")
    async def batch_read(from_type: str, to_type: str, request: Request):
        fkey, tkey = _object(from_type), _object(to_type)
        body = await request.json()
        inputs = body.get("inputs") or []
        if len(inputs) > 1000:
            raise HTTPException(status_code=400, detail={"message": "batch too large"})
        results = []
        for item in inputs:
            fid = str(item.get("id"))
            links = fake.data.assoc.get((fkey, tkey, fid), [])
            if not links:
                continue
            by_to: dict[str, list[dict]] = {}
            for l in links:
                by_to.setdefault(l["toObjectId"], []).append({"category": l["category"], "typeId": l["typeId"], "label": l["label"]})
            results.append({"from": {"id": fid}, "to": [{"toObjectId": tid, "associationTypes": types} for tid, types in by_to.items()]})
        return {"status": "COMPLETE", "results": results, "startedAt": _iso(BASE_TS), "completedAt": _iso(BASE_TS)}

    @api.get("/_fake/counts")
    async def counts():
        return {"counts": fake.counts(), "requests": fake.requests}

    return api


app = create_app(FakeHubSpot.from_env())


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the fake HubSpot API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args()
    print("fake HubSpot counts:", app.state.fake.counts())
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
