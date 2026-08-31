"""
Static-criteria lead scoring routes (read-only).

Scores are computed from the declarative registry in app.scoring.criteria —
no CRM writes here; write-back is a later, gated phase. The batch scan is
cached like the health scans; the single-record endpoint is one HubSpot call
and always fresh.
"""

from collections import Counter

from fastapi import APIRouter

from app.cache import scoring_cache
from app.config import settings
from app.routes.hubspot import hs_get, iter_contact_pages
from app.scoring.criteria import (
    CONTACT_BANDS,
    CONTACT_MAX_SCORE,
    CONTACT_PROPERTIES,
    CONTACT_RULES,
)
from app.scoring.engine import (
    contact_features_from_api,
    rules_as_dicts,
    score_features,
)

router = APIRouter(prefix="/api/scoring", tags=["scoring"])

# How many fully-detailed rows (with per-criterion breakdown) the batch
# response carries at each end of the ranking; the slim `scores` list is
# never capped — Phase 2's diff-sync reads it.
DETAIL_ROWS = 50


CAP = 10000


def _score_row(record: dict) -> dict:
    """Raw v3 contact item → detailed row (identity + score + breakdown).

    Empty identity fields stay empty strings — display placeholders ("—")
    belong to the frontend, and Phase 2 write-back reads this payload.
    """
    features = contact_features_from_api(record)
    scored = score_features(features)
    return {
        "id": record["id"],
        "name": features["name"],
        "email": features["email"],
        **scored,
    }


async def _compute_contact_scores() -> dict:
    """Score every contact against the static criteria registry."""
    rows: list[dict] = []
    async for page in iter_contact_pages(",".join(CONTACT_PROPERTIES), cap=CAP):
        rows.extend(_score_row(r) for r in page)
    capped = len(rows) >= CAP
    # Deterministic ranking: score, then id, so equal scores don't reshuffle
    # between scans.
    rows.sort(key=lambda r: (r["score"], r["id"]))

    total = len(rows)
    band_counts = Counter(r["band"] for r in rows)
    return {
        "object_type": "contacts",
        "scanned": total,
        "capped": capped,
        "max_score": CONTACT_MAX_SCORE,
        "average_score": round(sum(r["score"] for r in rows) / total, 1) if total else 0,
        "band_counts": {b.label: band_counts.get(b.label, 0) for b in CONTACT_BANDS},
        # Slim and UNCAPPED — the Phase 2 write-back contract.
        "scores": [{"id": r["id"], "score": r["score"], "band": r["band"]} for r in rows],
        "lowest": rows[:DETAIL_ROWS],
        "highest": rows[-DETAIL_ROWS:][::-1],
        "criteria": rules_as_dicts(CONTACT_RULES),
    }


@router.get("/criteria")
async def get_criteria():
    """Echo the scoring registry: rules, bands, fetched properties, max score."""
    return {
        "object_type": "contacts",
        "max_score": CONTACT_MAX_SCORE,
        "rules": rules_as_dicts(CONTACT_RULES),
        "bands": [{"label": b.label, "min_points": b.min_points} for b in CONTACT_BANDS],
        "properties": list(CONTACT_PROPERTIES),
    }


@router.get("/contacts")
async def get_contact_scores(refresh: bool = False):
    """Serve the cached contact-score scan; ?refresh=true forces a rescan."""
    return await scoring_cache.get_or_compute(
        "contact-scores",
        _compute_contact_scores,
        settings.scoring_cache_ttl_seconds,
        refresh=refresh,
    )


@router.get("/contacts/{contact_id}")
async def get_contact_score(contact_id: str):
    """Score one contact, always fresh (a single HubSpot call, uncached)."""
    record = await hs_get(
        f"/crm/v3/objects/contacts/{contact_id}",
        {"properties": ",".join(CONTACT_PROPERTIES), "associations": "companies"},
    )
    features = contact_features_from_api(record)
    return {
        "id": record.get("id", contact_id),
        "name": features["name"],
        "email": features["email"],
        "features": features,
        **score_features(features),
    }
