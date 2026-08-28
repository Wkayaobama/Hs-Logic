"""
HubSpot CRM API v3 explorer routes.
Uses the private app token from config to query the HubSpot API.
"""

import asyncio
import re
from collections import defaultdict
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.cache import health_cache
from app.config import settings

router = APIRouter(prefix="/api/hubspot", tags=["hubspot"])

HUBSPOT_BASE = "https://api.hubapi.com"


def hs_headers() -> dict:
    """Return auth headers for HubSpot private app token."""
    if not settings.hubspot_token:
        raise HTTPException(status_code=500, detail="HubSpot token not configured.")
    return {
        "Authorization": f"Bearer {settings.hubspot_token}",
        "Content-Type": "application/json",
    }


async def hs_get(path: str, params: dict | None = None) -> dict:
    """Make a GET request to the HubSpot API and return JSON."""
    url = f"{HUBSPOT_BASE}{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=hs_headers(), params=params or {})
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="HubSpot token is invalid or expired.")
    if resp.status_code == 403:
        raise HTTPException(status_code=403, detail="HubSpot token lacks required scopes.")
    if not resp.is_success:
        detail = resp.json().get("message", resp.text) if resp.content else resp.reason_phrase
        raise HTTPException(status_code=resp.status_code, detail=f"HubSpot error: {detail}")
    return resp.json()


async def hs_post(path: str, body: dict) -> dict:
    """Make a POST request to the HubSpot API and return JSON."""
    url = f"{HUBSPOT_BASE}{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=hs_headers(), json=body)
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="HubSpot token is invalid or expired.")
    if resp.status_code == 403:
        raise HTTPException(status_code=403, detail="HubSpot token lacks required scopes.")
    if not resp.is_success:
        detail = resp.json().get("message", resp.text) if resp.content else resp.reason_phrase
        raise HTTPException(status_code=resp.status_code, detail=f"HubSpot error: {detail}")
    return resp.json()


# ─── Portal / Account ───────────────────────────────────────────────────────


@router.get("/portal")
async def get_portal_info():
    """Return account details and CRM object counts."""
    # Account info
    account = await hs_get("/account-info/v3/details")

    # The plain list envelope carries no `total` (it is a search-API-only
    # field), so ask the search endpoint for it; -1 signals "unavailable"
    # and renders as an em dash in the frontend.
    async def count(obj: str) -> int:
        try:
            data = await hs_post(f"/crm/v3/objects/{obj}/search", {"limit": 1})
            total = data.get("total")
            return total if isinstance(total, int) else -1
        except Exception:
            return -1

    contacts_count = await count("contacts")
    companies_count = await count("companies")
    deals_count = await count("deals")
    tickets_count = await count("tickets")

    return {
        "portal_id": account.get("portalId"),
        "hub_domain": account.get("hubDomain"),
        "hub_name": account.get("companyName") or account.get("hub_domain", "—"),
        "timezone": account.get("timeZone"),
        "currency": account.get("defaultHubspotCurrency"),
        "created_at": account.get("createdAt"),
        "counts": {
            "contacts": contacts_count,
            "companies": companies_count,
            "deals": deals_count,
            "tickets": tickets_count,
        },
    }


# ─── Contacts ────────────────────────────────────────────────────────────────


@router.get("/contacts")
async def get_contacts(limit: int = 20, after: str | None = None):
    """Return recent contacts sorted by creation date."""
    params = {
        "limit": min(limit, 100),
        "properties": "firstname,lastname,email,phone,company,createdate,hs_lead_status,lifecyclestage",
        "sorts": ["-createdate"],
    }
    if after:
        params["after"] = after

    data = await hs_get("/crm/v3/objects/contacts", params)

    contacts = []
    for r in data.get("results", []):
        p = r.get("properties", {})
        contacts.append(
            {
                "id": r["id"],
                "firstname": p.get("firstname") or "",
                "lastname": p.get("lastname") or "",
                "email": p.get("email") or "",
                "phone": p.get("phone") or "",
                "company": p.get("company") or "",
                "lifecycle": p.get("lifecyclestage") or "",
                "lead_status": p.get("hs_lead_status") or "",
                "created_at": p.get("createdate") or "",
            }
        )

    paging = data.get("paging", {})
    return {
        "results": contacts,
        "total": data.get("total", 0),
        "next_after": paging.get("next", {}).get("after"),
    }


# ─── Companies ───────────────────────────────────────────────────────────────


@router.get("/companies")
async def get_companies(limit: int = 20, after: str | None = None):
    """Return recent companies sorted by creation date."""
    params = {
        "limit": min(limit, 100),
        "properties": "name,domain,industry,city,country,annualrevenue,numberofemployees,createdate,hs_lead_status",
        "sorts": ["-createdate"],
    }
    if after:
        params["after"] = after

    data = await hs_get("/crm/v3/objects/companies", params)

    companies = []
    for r in data.get("results", []):
        p = r.get("properties", {})
        companies.append(
            {
                "id": r["id"],
                "name": p.get("name") or "—",
                "domain": p.get("domain") or "",
                "industry": p.get("industry") or "",
                "city": p.get("city") or "",
                "country": p.get("country") or "",
                "revenue": p.get("annualrevenue") or "",
                "employees": p.get("numberofemployees") or "",
                "created_at": p.get("createdate") or "",
            }
        )

    paging = data.get("paging", {})
    return {
        "results": companies,
        "total": data.get("total", 0),
        "next_after": paging.get("next", {}).get("after"),
    }


# ─── Deals ───────────────────────────────────────────────────────────────────


@router.get("/deals")
async def get_deals(limit: int = 20, after: str | None = None):
    """Return recent deals sorted by creation date."""
    params = {
        "limit": min(limit, 100),
        "properties": "dealname,amount,dealstage,pipeline,closedate,createdate,hs_deal_stage_probability,dealtype",
        "sorts": ["-createdate"],
    }
    if after:
        params["after"] = after

    data = await hs_get("/crm/v3/objects/deals", params)

    deals = []
    for r in data.get("results", []):
        p = r.get("properties", {})
        deals.append(
            {
                "id": r["id"],
                "name": p.get("dealname") or "Untitled deal",
                "amount": p.get("amount") or "",
                "stage": p.get("dealstage") or "",
                "pipeline": p.get("pipeline") or "",
                "close_date": p.get("closedate") or "",
                "probability": p.get("hs_deal_stage_probability") or "",
                "deal_type": p.get("dealtype") or "",
                "created_at": p.get("createdate") or "",
            }
        )

    paging = data.get("paging", {})
    return {
        "results": deals,
        "total": data.get("total", 0),
        "next_after": paging.get("next", {}).get("after"),
    }


# ─── Pipelines ───────────────────────────────────────────────────────────────


@router.get("/pipelines")
async def get_pipelines():
    """Return deal pipelines and their stages."""
    data = await hs_get("/crm/v3/pipelines/deals")
    pipelines = []
    for p in data.get("results", []):
        pipelines.append(
            {
                "id": p["id"],
                "label": p.get("label") or p["id"],
                "stages": [
                    {
                        "id": s["id"],
                        "label": s.get("label") or s["id"],
                        "probability": s.get("metadata", {}).get("probability") or "0",
                        "display_order": s.get("displayOrder", 0),
                    }
                    for s in sorted(
                        p.get("stages", []), key=lambda x: x.get("displayOrder", 0)
                    )
                ],
            }
        )
    return {"pipelines": pipelines}


# ─── Tickets ─────────────────────────────────────────────────────────────────


@router.get("/tickets")
async def get_tickets(limit: int = 20, after: str | None = None):
    """Return recent support tickets."""
    params = {
        "limit": min(limit, 100),
        "properties": "subject,content,hs_ticket_priority,hs_pipeline_stage,createdate,hs_lastmodifieddate",
        "sorts": ["-createdate"],
    }
    if after:
        params["after"] = after

    data = await hs_get("/crm/v3/objects/tickets", params)

    tickets = []
    for r in data.get("results", []):
        p = r.get("properties", {})
        tickets.append(
            {
                "id": r["id"],
                "subject": p.get("subject") or "No subject",
                "priority": p.get("hs_ticket_priority") or "",
                "stage": p.get("hs_pipeline_stage") or "",
                "created_at": p.get("createdate") or "",
                "updated_at": p.get("hs_lastmodifieddate") or "",
            }
        )

    paging = data.get("paging", {})
    return {
        "results": tickets,
        "total": data.get("total", 0),
        "next_after": paging.get("next", {}).get("after"),
    }


# ─── CRM Health ──────────────────────────────────────────────────────────────


def _normalize(s: str) -> str:
    """Lowercase, collapse whitespace."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


async def _compute_crm_health() -> dict:
    """
    Compute CRM health metrics using the same logic as the SQL queries:

    Orphan companies
    ----------------
    A company is an orphan when ALL of the following are true:
      1. It has zero deal associations (deal_count = 0)
      2. It is NOT an edge-company — i.e. it is not directly associated
         with any deal that has a non-empty `final_customer` field
      3. Its normalised name is NOT in the set of `final_customer` name
         strings from any deal

    Duplicate companies
    -------------------
    Companies are grouped by normalised domain (non-empty) and by normalised
    name.  Any group with ≥ 2 members is a duplicate cluster.
    """
    CAP = 10000  # guard against very large portals

    # ── Step 1: page through ALL companies, inline deal associations ─────
    all_companies: list[dict] = []
    co_cursor: str | None = None
    while len(all_companies) < CAP:
        params: dict = {
            "limit": 100,
            "properties": "name,domain",
            "associations": "deals",
        }
        if co_cursor:
            params["after"] = co_cursor
        data = await hs_get("/crm/v3/objects/companies", params)
        for r in data.get("results", []):
            p = r.get("properties", {})
            deal_results = r.get("associations", {}).get("deals", {}).get("results", [])
            all_companies.append(
                {
                    "id": r["id"],
                    "name": p.get("name") or "",
                    "domain": p.get("domain") or "",
                    "deal_ids": [a["id"] for a in deal_results],
                }
            )
        co_cursor = data.get("paging", {}).get("next", {}).get("after")
        if not co_cursor:
            break

    # ── Step 2: search deals that have `final_customer` set ─────────────
    fc_deals: list[dict] = []
    search_cursor: str | None = None
    while len(fc_deals) < CAP:
        body: dict = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "final_customer",
                            "operator": "HAS_PROPERTY",
                        }
                    ]
                }
            ],
            "properties": ["final_customer"],
            "limit": 100,
        }
        if search_cursor:
            body["after"] = search_cursor
        try:
            sdata = await hs_post("/crm/v3/objects/deals/search", body)
        except HTTPException:
            # `final_customer` property doesn't exist on this portal — skip
            break
        for r in sdata.get("results", []):
            fc = (r.get("properties") or {}).get("final_customer") or ""
            if fc.strip():
                fc_deals.append({"id": r["id"], "final_customer": fc.strip()})
        search_cursor = sdata.get("paging", {}).get("next", {}).get("after")
        if not search_cursor:
            break

    # fc_names: normalised set of final_customer strings
    fc_names: set[str] = {_normalize(d["final_customer"]) for d in fc_deals}

    # ── Step 3: batch-read company associations for fc deals → edge set ──
    edge_company_ids: set[str] = set()
    fc_deal_ids = [d["id"] for d in fc_deals]
    for i in range(0, len(fc_deal_ids), 100):
        chunk = fc_deal_ids[i : i + 100]
        try:
            adata = await hs_post(
                "/crm/v4/associations/deals/companies/batch/read",
                {"inputs": [{"id": did} for did in chunk]},
            )
            for result in adata.get("results", []):
                for assoc in result.get("to", []):
                    edge_company_ids.add(str(assoc["toObjectId"]))
        except HTTPException:
            pass  # association scope may be absent; skip

    # ── Step 4: orphan detection ─────────────────────────────────────────
    orphans: list[dict] = []
    for c in all_companies:
        if len(c["deal_ids"]) > 0:
            continue
        if c["id"] in edge_company_ids:
            continue
        if _normalize(c["name"]) in fc_names:
            continue
        orphans.append({"id": c["id"], "name": c["name"] or "—", "domain": c["domain"] or ""})

    # ── Step 5: duplicate detection ─────────────────────────────────────
    domain_groups: dict[str, list] = defaultdict(list)
    name_groups: dict[str, list] = defaultdict(list)
    for c in all_companies:
        nd = _normalize(c["domain"])
        nn = _normalize(c["name"])
        if nd:
            domain_groups[nd].append(c)
        if nn:
            name_groups[nn].append(c)

    seen_cluster_ids: set[frozenset] = set()
    duplicate_clusters: list[dict] = []

    def _add_clusters(groups: dict[str, list], cluster_type: str) -> None:
        for key, group in sorted(groups.items()):
            if len(group) < 2:
                continue
            ids = frozenset(c["id"] for c in group)
            if ids in seen_cluster_ids:
                continue
            seen_cluster_ids.add(ids)
            duplicate_clusters.append(
                {
                    "type": cluster_type,
                    "key": key,
                    "count": len(group),
                    "companies": [
                        {"id": c["id"], "name": c["name"] or "—", "domain": c["domain"]}
                        for c in group[:10]
                    ],
                }
            )

    _add_clusters(domain_groups, "domain")
    _add_clusters(name_groups, "name")
    duplicate_clusters.sort(key=lambda x: -x["count"])

    # Collect every orphan + every company that appears in a duplicate cluster
    dup_company_ids = list(
        {c["id"] for cl in duplicate_clusters for c in cl["companies"]}
    )
    return {
        "scanned_companies": len(all_companies),
        "fc_deal_count": len(fc_deals),
        "edge_company_count": len(edge_company_ids),
        "orphan_count": len(orphans),
        "duplicate_cluster_count": len(duplicate_clusters),
        "orphans": orphans[:150],
        "duplicate_clusters": duplicate_clusters[:60],
        # Full ID lists for suppression export (not capped)
        "orphan_ids": [c["id"] for c in orphans],
        "duplicate_ids": dup_company_ids,
        "capped": len(all_companies) >= CAP,
    }


@router.get("/crm-health")
async def get_crm_health(refresh: bool = False):
    """Serve the cached CRM health scan; ?refresh=true forces a rescan."""
    return await health_cache.get_or_compute(
        "crm-health",
        _compute_crm_health,
        settings.health_cache_ttl_seconds,
        refresh=refresh,
    )


# ─── Contact Health ───────────────────────────────────────────────────────────

# Lifecycle stages ranked; anything at index >= 2 is MQL-eligible
_MQL_LIFECYCLES = {
    "marketingqualifiedlead",
    "salesqualifiedlead",
    "opportunity",
    "customer",
    "evangelist",
}


def _contact_missing(c: dict) -> list[str]:
    missing = []
    if not c["email"]:
        missing.append("email")
    if not c["phone"]:
        missing.append("phone")
    if not (c["firstname"] or c["lastname"]):
        missing.append("name")
    if not c["company_ids"]:
        missing.append("company")
    return missing


def _contact_display_name(c: dict) -> str:
    return f"{c['firstname']} {c['lastname']}".strip() or "—"


async def _compute_contact_health() -> dict:
    """
    Contact quality analysis — mirrors the SQL CTE pattern applied to contacts.

    NQL (not-qualified-for-CRM)
    ─────────────────────────────
    A contact is NQL when it is BOTH unreachable AND anonymous, i.e. any of:
      · No email AND no phone  (cannot be contacted by any channel)
      · No firstname AND no lastname  (completely anonymous record)
    These are records too sparse to be actionable in the CRM.

    _MQL (marketing-qualified)
    ─────────────────────────────
    A contact meets the minimum MQL bar when it has ALL of:
      · An email address
      · A first or last name
      · At least one company association
    OR its HubSpot lifecyclestage is already mql / sql / opportunity /
    customer / evangelist (HubSpot itself already promotes it).

    Likely duplicates
    ─────────────────────────────
    · Contacts sharing the same normalised email  →  "email" cluster
    · Contacts sharing the same normalised full name  →  "name" cluster
    · Contacts linked to 2+ companies (violates single-company cardinality)
      →  reported separately as multi-company contacts
    """
    CAP = 10000

    # ── Step 1: page through all contacts ───────────────────────────────
    all_contacts: list[dict] = []
    cursor: str | None = None
    while len(all_contacts) < CAP:
        params: dict = {
            "limit": 100,
            "properties": "email,firstname,lastname,phone,lifecyclestage,hs_lead_status,createdate",
            "associations": "companies",
        }
        if cursor:
            params["after"] = cursor
        data = await hs_get("/crm/v3/objects/contacts", params)
        for r in data.get("results", []):
            p = r.get("properties", {})
            company_results = r.get("associations", {}).get("companies", {}).get("results", [])
            all_contacts.append(
                {
                    "id": r["id"],
                    "email": (p.get("email") or "").strip(),
                    "firstname": (p.get("firstname") or "").strip(),
                    "lastname": (p.get("lastname") or "").strip(),
                    "phone": (p.get("phone") or "").strip(),
                    "lifecycle": (p.get("lifecyclestage") or "").strip().lower(),
                    "lead_status": (p.get("hs_lead_status") or "").strip(),
                    "company_ids": list({a["id"] for a in company_results}),
                    "created_at": p.get("createdate") or "",
                }
            )
        cursor = data.get("paging", {}).get("next", {}).get("after")
        if not cursor:
            break

    # ── Step 2: classify each contact ───────────────────────────────────
    nql_contacts: list[dict] = []
    mql_contacts: list[dict] = []
    borderline_contacts: list[dict] = []

    # counters for missing-field heat-map
    missing_counts: dict[str, int] = {"email": 0, "phone": 0, "name": 0, "company": 0}

    for c in all_contacts:
        has_email = bool(c["email"])
        has_phone = bool(c["phone"])
        has_name = bool(c["firstname"] or c["lastname"])
        has_company = len(c["company_ids"]) > 0

        # accumulate missing field stats across ALL contacts
        if not has_email:
            missing_counts["email"] += 1
        if not has_phone:
            missing_counts["phone"] += 1
        if not has_name:
            missing_counts["name"] += 1
        if not has_company:
            missing_counts["company"] += 1

        # NQL: unreachable (no email AND no phone) OR truly anonymous (no name)
        is_nql = (not has_email and not has_phone) or (not has_name)

        # MQL: meets minimum bar or HubSpot already classified it
        is_mql = (has_email and has_name and has_company) or (
            c["lifecycle"] in _MQL_LIFECYCLES
        )

        row = {
            "id": c["id"],
            "name": _contact_display_name(c),
            "email": c["email"],
            "phone": c["phone"],
            "lifecycle": c["lifecycle"],
            "lead_status": c["lead_status"],
            "company_count": len(c["company_ids"]),
            "created_at": c["created_at"],
        }

        if is_nql:
            nql_contacts.append({**row, "missing": _contact_missing(c)})
        elif is_mql:
            mql_contacts.append(row)
        else:
            borderline_contacts.append({**row, "missing": _contact_missing(c)})

    # ── Step 3: duplicate detection ─────────────────────────────────────
    email_groups: dict[str, list] = defaultdict(list)
    name_groups: dict[str, list] = defaultdict(list)
    multi_company: list[dict] = []

    for c in all_contacts:
        norm_email = c["email"].lower() if c["email"] else ""
        norm_name = _normalize(f"{c['firstname']} {c['lastname']}")

        if norm_email:
            email_groups[norm_email].append(c)
        if norm_name:
            name_groups[norm_name].append(c)

        if len(c["company_ids"]) > 1:
            multi_company.append(
                {
                    "id": c["id"],
                    "name": _contact_display_name(c),
                    "email": c["email"],
                    "company_count": len(c["company_ids"]),
                }
            )

    seen_dup_ids: set[frozenset] = set()
    dup_clusters: list[dict] = []

    def _add_contact_clusters(groups: dict[str, list], cluster_type: str) -> None:
        for key, group in sorted(groups.items()):
            if len(group) < 2:
                continue
            ids = frozenset(c["id"] for c in group)
            if ids in seen_dup_ids:
                continue
            seen_dup_ids.add(ids)
            dup_clusters.append(
                {
                    "type": cluster_type,
                    "key": key,
                    "count": len(group),
                    "contacts": [
                        {
                            "id": c["id"],
                            "name": _contact_display_name(c),
                            "email": c["email"],
                            "lifecycle": c["lifecycle"],
                        }
                        for c in group[:10]
                    ],
                }
            )

    _add_contact_clusters(email_groups, "email")
    _add_contact_clusters(name_groups, "name")
    dup_clusters.sort(key=lambda x: -x["count"])

    total = len(all_contacts)
    return {
        "scanned_contacts": total,
        "nql_count": len(nql_contacts),
        "mql_count": len(mql_contacts),
        "borderline_count": len(borderline_contacts),
        "duplicate_cluster_count": len(dup_clusters),
        "multi_company_count": len(multi_company),
        # missing field prevalence across ALL contacts (not just NQL)
        "missing_prevalence": {
            k: {"count": v, "pct": round(100 * v / total, 1) if total else 0}
            for k, v in missing_counts.items()
        },
        "nql_contacts": nql_contacts[:150],
        "mql_contacts": mql_contacts[:50],
        "borderline_contacts": borderline_contacts[:50],
        "duplicate_clusters": dup_clusters[:60],
        "multi_company_contacts": multi_company[:50],
        # Full ID lists for suppression export (not capped)
        "nql_ids": [c["id"] for c in nql_contacts],
        "duplicate_ids": list({c["id"] for cl in dup_clusters for c in cl["contacts"]}),
        "multi_company_ids": [c["id"] for c in multi_company],
        "capped": len(all_contacts) >= CAP,
    }


@router.get("/contact-health")
async def get_contact_health(refresh: bool = False):
    """Serve the cached contact health scan; ?refresh=true forces a rescan."""
    return await health_cache.get_or_compute(
        "contact-health",
        _compute_contact_health,
        settings.health_cache_ttl_seconds,
        refresh=refresh,
    )


# ─── Suppression list export ──────────────────────────────────────────────────


class SuppressionRequest(BaseModel):
    contact_ids: list[str]
    list_name: str = ""


@router.post("/suppression-list")
async def create_suppression_list(req: SuppressionRequest):
    """
    Create a static HubSpot contact list and bulk-add the supplied contact IDs.

    Uses the Lists API v3:
      POST /crm/v3/lists                          → create MANUAL (static) list
      PUT  /crm/v3/lists/{id}/memberships/add     → add up to 100 IDs per call

    Required private-app scope: crm.lists.write

    On scope error (403) the frontend falls back to a CSV download automatically.

    Returns: {success, list_id, list_name, added, errors, url}
    """
    if not req.contact_ids:
        raise HTTPException(status_code=400, detail="No contact IDs provided.")

    headers = hs_headers()  # raises 500 if token missing

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    name = req.list_name.strip() or f"Suppression — Health Check ({ts})"

    CHUNK = 100      # HubSpot Lists API membership-add limit per request
    ADD_DELAY = 0.12  # 120 ms between batch-add calls (rate-limit headroom)

    async with httpx.AsyncClient(timeout=30.0) as client:

        # ── Step 1: create the static list ───────────────────────────────
        create_r = await client.post(
            f"{HUBSPOT_BASE}/crm/v3/lists",
            headers=headers,
            json={
                "name":           name,
                "objectTypeId":   "0-1",      # 0-1 = contacts
                "processingType": "MANUAL",   # static / suppression list
            },
        )
        if create_r.status_code not in (200, 201):
            raise HTTPException(
                status_code=create_r.status_code,
                detail=f"HubSpot Lists API error: {create_r.text[:400]}",
            )

        list_data = create_r.json()
        list_id = list_data.get("listId") or list_data.get("id")
        if not list_id:
            raise HTTPException(
                status_code=500,
                detail=f"List created but no listId in response: {list_data}",
            )

        # ── Step 2: add contacts in chunks of 100 ────────────────────────
        ids = req.contact_ids
        added = 0
        errors = 0

        for start in range(0, len(ids), CHUNK):
            chunk = ids[start : start + CHUNK]
            add_r = await client.put(
                f"{HUBSPOT_BASE}/crm/v3/lists/{list_id}/memberships/add",
                headers=headers,
                json={"recordIdsToAdd": chunk},
            )
            if add_r.status_code in (200, 204):
                added += len(chunk)
            else:
                errors += len(chunk)

            # Rate-limit headroom between batch-add requests
            if start + CHUNK < len(ids):
                await asyncio.sleep(ADD_DELAY)

    portal_id = settings.hubspot_portal_id
    list_url = (
        f"https://app.hubspot.com/contacts/{portal_id}/lists/{list_id}"
        if portal_id
        else f"https://app.hubspot.com/contacts/lists/{list_id}"
    )

    return {
        "success":   True,
        "list_id":   list_id,
        "list_name": name,
        "added":     added,
        "errors":    errors,
        "url":       list_url,
    }
