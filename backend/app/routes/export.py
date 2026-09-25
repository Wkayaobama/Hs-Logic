"""Bulk export, count, association and metadata routes used by the sampling probe.

Every response carries ``X-Logic-*`` headers (request id, duration, HubSpot
request count, 429s) so the probe can measure the execution layer, and a
``stats`` block with the same numbers in the body.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.config import settings
from app.entities import ENGAGEMENTS, Marker, get_registry
from app.hubspot.client import RequestStats, get_client

router = APIRouter(prefix="/api", tags=["export"])

OBJECT_TYPES = ["contacts", "companies", "deals", "tickets", "notes", "calls", "meetings", "tasks"]

BASE_PROPERTIES: dict[str, list[str]] = {
    "contacts": [
        "email", "firstname", "lastname", "company", "jobtitle", "lifecyclestage",
        "hs_lead_status", "hubspot_owner_id", "hubspot_team_id", "createdate",
        "lastmodifieddate", "hs_all_assigned_business_unit_ids",
    ],
    "companies": [
        "name", "domain", "industry", "country", "city", "hubspot_owner_id",
        "hubspot_team_id", "createdate", "hs_lastmodifieddate",
        "hs_all_assigned_business_unit_ids",
    ],
    "deals": [
        "dealname", "amount", "amount_in_home_currency", "deal_currency_code", "pipeline",
        "dealstage", "closedate", "createdate", "hs_lastmodifieddate", "hubspot_owner_id",
        "hubspot_team_id", "hs_is_closed", "hs_is_closed_won", "prod___product_name_new_",
        "product_line", "product_hierarchy", "wisekey___seal", "icalps__sealsq",
        "sales_region", "final_customer", "revenue_state", "notes_last_updated",
        "hs_all_assigned_business_unit_ids",
    ],
    "tickets": [
        "subject", "hs_pipeline", "hs_pipeline_stage", "hs_ticket_priority", "createdate",
        "hs_lastmodifieddate", "hubspot_owner_id", "hs_all_assigned_business_unit_ids",
    ],
    "notes": ["hs_timestamp", "hs_lastmodifieddate", "hubspot_owner_id"],
    "calls": ["hs_timestamp", "hs_lastmodifieddate", "hubspot_owner_id", "hs_call_disposition"],
    "meetings": ["hs_timestamp", "hs_lastmodifieddate", "hubspot_owner_id", "hs_meeting_outcome"],
    "tasks": ["hs_timestamp", "hs_lastmodifieddate", "hubspot_owner_id", "hs_task_status", "hs_task_type"],
}

DEFAULT_ASSOCIATIONS: dict[str, list[str]] = {
    "contacts": ["companies"],
    "companies": ["contacts", "deals"],
    "deals": ["companies", "contacts"],
    "tickets": ["companies", "contacts", "deals"],
    "notes": ["companies", "contacts", "deals"],
    "calls": ["companies", "contacts"],
    "meetings": ["companies", "contacts"],
    "tasks": ["companies", "contacts"],
}

FILTER_OPERATORS = {
    "EQ", "NEQ", "GT", "GTE", "LT", "LTE", "HAS_PROPERTY", "NOT_HAS_PROPERTY",
    "IN", "NOT_IN", "CONTAINS_TOKEN", "NOT_CONTAINS_TOKEN",
}


class AssociationRef(BaseModel):
    id: str
    typeId: int | None = None
    label: str | None = None
    category: str | None = None


class ExportRecord(BaseModel):
    id: str
    created_at: str | None = None
    updated_at: str | None = None
    archived: bool = False
    properties: dict[str, Any] = Field(default_factory=dict)
    associations: dict[str, list[AssociationRef]] = Field(default_factory=dict)


class ExportPage(BaseModel):
    object: str
    entity: str | None
    scope_mode: str
    results: list[ExportRecord]
    count: int
    next_after: str | None
    capped: bool
    no_marker: bool
    properties: list[str]
    associations: list[str]
    stats: dict


class CountResponse(BaseModel):
    object: str
    entity: str | None
    scope_mode: str
    method: str
    total: int
    capped: bool
    pages_walked: int = 0
    filters: list[dict] = Field(default_factory=list)
    stats: dict


# ── helpers ───────────────────────────────────────────────────────────────


def _validate_object(object_type: str) -> None:
    if object_type not in OBJECT_TYPES:
        raise HTTPException(
            status_code=404, detail=f"Unknown object type '{object_type}'. Known: {', '.join(OBJECT_TYPES)}"
        )


def _validate_entity(entity: str | None) -> str | None:
    if entity is None or entity == "":
        return None
    registry = get_registry()
    key = entity.upper()
    if key not in registry.ids:
        raise HTTPException(
            status_code=404, detail=f"Unknown entity '{entity}'. Known: {', '.join(registry.ids)}"
        )
    return key


def _resolve_properties(object_type: str, requested: str | None) -> list[str]:
    names = list(BASE_PROPERTIES.get(object_type, []))
    if requested:
        for name in requested.split(","):
            name = name.strip()
            if name and name not in names:
                names.append(name)
    if object_type not in ENGAGEMENTS:
        for name in get_registry().marker_properties(object_type):
            if name not in names:
                names.append(name)
    for name in ("hs_object_id",):
        if name not in names:
            names.append(name)
    return names


def _resolve_associations(object_type: str, requested: str | None) -> list[str]:
    if requested is None:
        return list(DEFAULT_ASSOCIATIONS.get(object_type, []))
    out = []
    for name in requested.split(","):
        name = name.strip()
        if not name:
            continue
        if name not in OBJECT_TYPES:
            raise HTTPException(status_code=400, detail=f"Unknown association target '{name}'.")
        if name != object_type and name not in out:
            out.append(name)
    return out


def _parse_filter(raw: str) -> dict:
    """``prop:OP:value`` -> HubSpot filter; IN/NOT_IN take ``a|b|c``."""
    parts = raw.split(":", 2)
    if len(parts) < 2:
        raise HTTPException(status_code=400, detail=f"Bad filter '{raw}': expected prop:OP[:value].")
    prop, op = parts[0].strip(), parts[1].strip().upper()
    if op not in FILTER_OPERATORS:
        raise HTTPException(status_code=400, detail=f"Bad filter operator '{op}' in '{raw}'.")
    if op in ("HAS_PROPERTY", "NOT_HAS_PROPERTY"):
        return {"propertyName": prop, "operator": op}
    if len(parts) < 3:
        raise HTTPException(status_code=400, detail=f"Filter '{raw}' needs a value.")
    value = parts[2]
    if op in ("IN", "NOT_IN"):
        return {"propertyName": prop, "operator": op, "values": [v for v in value.split("|") if v != ""]}
    return {"propertyName": prop, "operator": op, "value": value}


def _filter_to_marker(f: dict) -> Marker | None:
    op = f.get("operator")
    if op not in ("EQ", "NEQ", "HAS_PROPERTY", "NOT_HAS_PROPERTY", "IN", "NOT_IN", "CONTAINS_TOKEN"):
        return None
    return Marker(
        property=f["propertyName"], operator=op, value=f.get("value"), values=f.get("values", [])
    )


def _attach(response: Response, stats: RequestStats) -> None:
    for key, value in stats.headers().items():
        response.headers[key] = value


# ── routes ────────────────────────────────────────────────────────────────


@router.get("/export/associations")
async def export_associations(
    response: Response,
    from_type: str = Query(..., alias="from"),
    to_type: str = Query(..., alias="to"),
    ids: str = Query(..., description="comma-separated record ids"),
):
    """v4 batch association read with type ids and labels."""
    _validate_object(from_type)
    _validate_object(to_type)
    id_list = [i.strip() for i in ids.split(",") if i.strip()]
    if not id_list:
        raise HTTPException(status_code=400, detail="ids must not be empty.")
    stats = RequestStats()
    data = await get_client().batch_read_associations(from_type, to_type, id_list, stats=stats)
    _attach(response, stats)
    return {"from": from_type, "to": to_type, "results": data, "stats": stats.as_dict()}


@router.get("/export/{object_type}", response_model=ExportPage)
async def export_page(
    object_type: str,
    response: Response,
    entity: str | None = None,
    after: str | None = None,
    limit: int | None = Query(None, ge=1, le=200),
    properties: str | None = None,
    associations: str | None = None,
    sample: int | None = Query(None, ge=1, le=200),
    with_labels: bool = True,
):
    """One page of records for an object, optionally scoped to a business entity."""
    _validate_object(object_type)
    entity_id = _validate_entity(entity)
    registry = get_registry()
    client = get_client()
    stats = RequestStats()

    props = _resolve_properties(object_type, properties)
    assoc_types = _resolve_associations(object_type, associations)
    page_limit = limit or settings.export_page_limit
    if sample:
        page_limit = min(page_limit, sample)

    mode, filter_groups = registry.scope(entity_id, object_type)
    results: list[dict] = []
    next_after: str | None = None

    if mode == "search":
        results, next_after, _ = await client.search_page(
            object_type,
            filter_groups=filter_groups,
            properties=props,
            after=after,
            limit=page_limit,
            stats=stats,
        )
    elif mode in ("all", "unscoped", "complement"):
        results, next_after = await client.list_page(
            object_type, properties=props, after=after, limit=page_limit, stats=stats
        )
        if mode == "complement":
            results = [
                r for r in results if registry.matches(entity_id, object_type, r.get("properties") or {})
            ]
    # mode == "no_marker": nothing to fetch

    if sample:
        results = results[:sample]
        next_after = None

    assoc_map: dict[str, dict[str, list[dict]]] = {}
    if with_labels and results and assoc_types:
        ids = [str(r["id"]) for r in results]
        for to_type in assoc_types:
            assoc_map[to_type] = await client.batch_read_associations(
                object_type, to_type, ids, stats=stats
            )

    records = [
        ExportRecord(
            id=str(r["id"]),
            created_at=r.get("createdAt"),
            updated_at=r.get("updatedAt"),
            archived=bool(r.get("archived", False)),
            properties=r.get("properties") or {},
            associations={
                to_type: [AssociationRef(**a) for a in assoc_map.get(to_type, {}).get(str(r["id"]), [])]
                for to_type in assoc_types
            },
        )
        for r in results
    ]
    _attach(response, stats)
    return ExportPage(
        object=object_type,
        entity=entity_id,
        scope_mode=mode,
        results=records,
        count=len(records),
        next_after=next_after,
        capped=stats.capped,
        no_marker=(mode == "no_marker"),
        properties=props,
        associations=assoc_types,
        stats=stats.as_dict(),
    )


@router.get("/export/{object_type}/count", response_model=CountResponse)
async def export_count(
    object_type: str,
    response: Response,
    entity: str | None = None,
    filter: list[str] = Query(default=[]),
    max_pages: int = Query(400, ge=1, le=5000),
):
    """Record count for an object, optionally scoped, with extra AND-ed filters
    (``filter=prop:OP:value``, repeatable) so reference questions can be asked directly."""
    _validate_object(object_type)
    entity_id = _validate_entity(entity)
    registry = get_registry()
    client = get_client()
    stats = RequestStats()
    extra = [_parse_filter(f) for f in filter]
    mode, filter_groups = registry.scope(entity_id, object_type)

    if mode == "no_marker":
        _attach(response, stats)
        return CountResponse(
            object=object_type, entity=entity_id, scope_mode=mode, method="no_marker",
            total=0, capped=False, filters=extra, stats=stats.as_dict(),
        )

    if mode in ("search", "all", "unscoped"):
        groups = filter_groups or []
        if extra:
            groups = [{"filters": g["filters"] + extra} for g in groups] if groups else [{"filters": extra}]
        _, _, total = await client.search_page(
            object_type,
            filter_groups=groups or None,
            properties=["hs_object_id"],
            after=None,
            limit=1,
            stats=stats,
        )
        _attach(response, stats)
        return CountResponse(
            object=object_type, entity=entity_id, scope_mode=mode, method="search",
            total=int(total or 0), capped=False, filters=extra, stats=stats.as_dict(),
        )

    # complement: walk the list endpoint and count client-side
    markers = [m for m in (_filter_to_marker(f) for f in extra) if m is not None]
    if len(markers) != len(extra):
        raise HTTPException(
            status_code=400,
            detail="Complement scope supports EQ/NEQ/IN/NOT_IN/HAS_PROPERTY/NOT_HAS_PROPERTY/CONTAINS_TOKEN filters only.",
        )
    props = _resolve_properties(object_type, ",".join(m.property for m in markers))
    total = 0
    after: str | None = None
    pages = 0
    capped = False
    while True:
        results, after = await client.list_page(
            object_type, properties=props, after=after, limit=100, stats=stats
        )
        pages += 1
        for r in results:
            p = r.get("properties") or {}
            if registry.matches(entity_id, object_type, p) and all(m.matches(p) for m in markers):
                total += 1
        if not after:
            break
        if pages >= max_pages:
            capped = True
            break
    _attach(response, stats)
    return CountResponse(
        object=object_type, entity=entity_id, scope_mode=mode, method="list-walk",
        total=total, capped=capped, pages_walked=pages, filters=extra, stats=stats.as_dict(),
    )


@router.get("/meta/entities")
async def meta_entities():
    """The loaded entity seed and the export defaults per object."""
    registry = get_registry()
    return {
        **registry.describe(),
        "object_types": OBJECT_TYPES,
        "default_properties": BASE_PROPERTIES,
        "default_associations": DEFAULT_ASSOCIATIONS,
        "marker_properties": {o: registry.marker_properties(o) for o in OBJECT_TYPES if o not in ENGAGEMENTS},
    }


@router.get("/meta/properties/{object_type}")
async def meta_properties(object_type: str, response: Response):
    """Property definitions (type, fieldType, groupName, hubspotDefined, options)."""
    _validate_object(object_type)
    stats = RequestStats()
    raw = await get_client().get_properties(object_type, stats=stats)
    trimmed = [
        {
            "name": p.get("name"),
            "label": p.get("label"),
            "type": p.get("type"),
            "fieldType": p.get("fieldType"),
            "groupName": p.get("groupName"),
            "description": p.get("description"),
            "hubspotDefined": p.get("hubspotDefined"),
            "calculated": p.get("calculated"),
            "hidden": p.get("hidden"),
            "options": [
                {"value": o.get("value"), "label": o.get("label"), "hidden": o.get("hidden")}
                for o in (p.get("options") or [])
            ],
        }
        for p in raw
    ]
    _attach(response, stats)
    return {"object": object_type, "count": len(trimmed), "results": trimmed, "stats": stats.as_dict()}
