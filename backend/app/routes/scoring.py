"""
Static-criteria lead scoring routes (read-only).

Scores are computed from the declarative registry in app.scoring.criteria —
no CRM writes here; write-back is a later, gated phase. The batch scan is
cached like the health scans; the single-record endpoint is one HubSpot call
and always fresh. /probe aggregates portal distributions so the operator can
configure the pending fit registries (roles/countries/sources/pages/
industries) from observed data instead of guesses.
"""

import time
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter

from app.cache import scoring_cache
from app.config import settings
from app.routes.hubspot import (
    _normalize,
    duplicate_id_sets,
    hs_get,
    iter_company_pages,
    iter_contact_pages,
)
from app.scoring.criteria import (
    CONTACT_BANDS,
    CONTACT_FULL_MODEL_SCORE,
    CONTACT_PROPERTIES,
    CONTACT_RULES,
    DISQUALIFIERS,
    FREEMAIL_DOMAINS,
    MQL_THRESHOLD,
    PROBE_COMPANY_PROPERTIES,
    PROBE_CONTACT_PROPERTIES,
)
from app.scoring.engine import (
    active_rules,
    contact_features_from_api,
    rules_as_dicts,
    score_features,
    tokenize,
)


def _active_max_score() -> int:
    return sum(rule.points for rule in active_rules())

router = APIRouter(prefix="/api/scoring", tags=["scoring"])

# How many fully-detailed rows (with per-criterion breakdown) the batch
# response carries at each end of the ranking; the slim `scores` list is
# never capped — the write-back phase reads it.
DETAIL_ROWS = 50

# Corpus signals from the most recent batch compute (per-process, like the
# caches): lets the single-record endpoint report duplicate status without
# re-scanning. None until the first batch scan has run; treated as expired
# (unavailable again) once older than the scoring cache TTL, so a stale
# duplicate set is never presented as current.
_last_duplicate_ids: set[str] | None = None
_last_corpus_at: float | None = None


def _fresh_corpus() -> dict | None:
    if _last_duplicate_ids is None or _last_corpus_at is None:
        return None
    if time.monotonic() - _last_corpus_at >= settings.scoring_cache_ttl_seconds:
        return None
    return {"duplicate_ids": _last_duplicate_ids}


async def _compute_contact_scores() -> dict:
    """Score every contact against the static criteria registry.

    Two passes: extract slim per-record features page-by-page (raw pages are
    dropped, keeping peak memory at one page), then derive the corpus-level
    duplicate set and score with it merged in.
    """
    global _last_duplicate_ids, _last_corpus_at
    cap = settings.scan_cap
    now = datetime.now(timezone.utc)

    entries: list[tuple[str, dict]] = []  # (contact id, extracted features)
    async for page in iter_contact_pages(",".join(CONTACT_PROPERTIES), cap=cap):
        for record in page:
            entries.append(
                (str(record["id"]), contact_features_from_api(record, now=now))
            )
    capped = len(entries) >= cap

    # Corpus stage — same grouping semantic as the health duplicate clusters.
    duplicate_ids = duplicate_id_sets(
        (cid, features["email"].lower()) for cid, features in entries
    ) | duplicate_id_sets(
        (cid, _normalize(features["name"])) for cid, features in entries
    )
    _last_duplicate_ids = duplicate_ids
    _last_corpus_at = time.monotonic()

    rows: list[dict] = []
    for cid, features in entries:
        features["is_unique"] = cid not in duplicate_ids
        scored = score_features(features)
        rows.append(
            {"id": cid, "name": features["name"], "email": features["email"], **scored}
        )
    # Deterministic ranking: score, then id, so equal scores don't reshuffle
    # between scans.
    rows.sort(key=lambda r: (r["score"], r["id"]))

    total = len(rows)
    band_counts = Counter(r["band"] for r in rows)
    classification_counts = Counter(r["classification"] for r in rows)
    return {
        "object_type": "contacts",
        "scanned": total,
        "capped": capped,
        "max_score": _active_max_score(),
        "full_model_score": CONTACT_FULL_MODEL_SCORE,
        "mql_threshold": MQL_THRESHOLD,
        "average_score": round(sum(r["score"] for r in rows) / total, 1) if total else 0,
        "band_counts": {b.label: band_counts.get(b.label, 0) for b in CONTACT_BANDS},
        "classification_counts": {
            label: classification_counts.get(label, 0)
            for label in ("mql", "borderline", "nql")
        },
        "duplicate_count": len(duplicate_ids),
        # Slim and UNCAPPED — the write-back contract.
        "scores": [
            {"id": r["id"], "score": r["score"], "band": r["band"],
             "classification": r["classification"]}
            for r in rows
        ],
        "lowest": rows[:DETAIL_ROWS],
        "highest": rows[-DETAIL_ROWS:][::-1],
        "criteria": rules_as_dicts(CONTACT_RULES),
        # Bands/gates ride along so the dashboard renders thresholds and
        # gate semantics from data instead of hardcoded copy.
        "bands": _bands_payload(),
        "gates": _gates_payload(),
    }


# ── Probe: distributions to configure the fit registries from ───────────────

# Recommendation hygiene only — the engine matches whatever the registry
# holds; these just keep glue words out of the suggested token list.
_TOKEN_STOPWORDS = frozenset({
    "and", "the", "for", "von", "der", "des", "les", "chez", "dans", "with",
    "of", "at", "in", "on", "to", "de", "du", "et", "la", "le", "di", "da",
})


def _top(counter: Counter, n: int = 40) -> list[dict]:
    return [{"value": value, "count": count} for value, count in counter.most_common(n)]


def _url_path_prefix(url: str) -> str:
    segments = [s for s in urlparse(url).path.split("/") if s]
    return "/" + "/".join(segments[:2]) if segments else "/"


async def _compute_probe() -> dict:
    """Aggregate the portal's observed values for each pending fit registry.

    Values are read through the SAME extractor the scoring rules use
    (contact_features_from_api) so the recommended lists reflect exactly
    what the rules will match against — fallback chains included.
    """
    cap = settings.scan_cap

    job_titles: Counter = Counter()
    job_title_tokens: Counter = Counter()
    countries: Counter = Counter()
    sources: Counter = Counter()
    email_domains: Counter = Counter()
    url_paths: Counter = Counter()
    domain_split = {"freemail": 0, "corporate": 0, "unknown": 0}

    scanned_contacts = 0
    async for page in iter_contact_pages(
        ",".join(PROBE_CONTACT_PROPERTIES), associations=None, cap=cap
    ):
        for record in page:
            scanned_contacts += 1
            features = contact_features_from_api(record)

            if features["jobtitle"]:
                job_titles[features["jobtitle"]] += 1
                for token in tokenize(features["jobtitle"]):
                    if token not in _TOKEN_STOPWORDS:
                        job_title_tokens[token] += 1

            if features["country"]:
                countries[features["country"]] += 1

            if features["source"]:
                sources[features["source"]] += 1

            domain = features["email_domain"]
            if domain:
                email_domains[domain] += 1
                bucket = "freemail" if domain in FREEMAIL_DOMAINS else "corporate"
                domain_split[bucket] += 1
            else:
                domain_split["unknown"] += 1

            if features["last_url"]:
                url_paths[_url_path_prefix(features["last_url"])] += 1

    industries: Counter = Counter()
    scanned_companies = 0
    async for page in iter_company_pages(",".join(PROBE_COMPANY_PROPERTIES), cap=cap):
        for record in page:
            scanned_companies += 1
            industry = ((record.get("properties") or {}).get("industry") or "").strip()
            if industry:
                industries[industry] += 1

    return {
        "scanned_contacts": scanned_contacts,
        "contacts_capped": scanned_contacts >= cap,
        "scanned_companies": scanned_companies,
        "companies_capped": scanned_companies >= cap,
        "email_domain_split": domain_split,
        "top": {
            "job_titles": _top(job_titles),
            "job_title_tokens": _top(job_title_tokens),
            "countries": _top(countries),
            "sources": _top(sources, 20),
            "email_domains": _top(email_domains),
            "url_paths": _top(url_paths),
            "industries": _top(industries),
        },
    }


# ── Routes ──────────────────────────────────────────────────────────────────


def _bands_payload() -> list[dict]:
    return [{"label": b.label, "min_points": b.min_points} for b in CONTACT_BANDS]


def _gates_payload() -> list[dict]:
    return [
        {"id": g.id, "label": g.label, "empty_features": list(g.empty_features)}
        for g in DISQUALIFIERS
    ]


@router.get("/criteria")
async def get_criteria():
    """Echo the scoring registry: rules (with configured flags), bands,
    gates, thresholds, and the fetched properties."""
    return {
        "object_type": "contacts",
        "max_score": _active_max_score(),
        "full_model_score": CONTACT_FULL_MODEL_SCORE,
        "mql_threshold": MQL_THRESHOLD,
        "rules": rules_as_dicts(CONTACT_RULES),
        "bands": _bands_payload(),
        "gates": _gates_payload(),
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


@router.get("/probe")
async def get_probe(refresh: bool = False):
    """Serve the cached fit-registry probe; ?refresh=true forces a rescan."""
    return await scoring_cache.get_or_compute(
        "probe",
        _compute_probe,
        settings.scoring_cache_ttl_seconds,
        refresh=refresh,
    )


@router.get("/contacts/{contact_id}")
async def get_contact_score(contact_id: str):
    """Score one contact, always fresh (a single HubSpot call, uncached).

    Corpus signals (duplicate status) come from the most recent batch scan
    when one has run in this process; otherwise they are reported unknown.
    """
    record = await hs_get(
        f"/crm/v3/objects/contacts/{contact_id}",
        {"properties": ",".join(CONTACT_PROPERTIES), "associations": "companies"},
    )
    corpus = _fresh_corpus()
    features = contact_features_from_api(
        record, corpus=corpus, now=datetime.now(timezone.utc)
    )
    return {
        "id": record.get("id", contact_id),
        "name": features["name"],
        "email": features["email"],
        "corpus": "cached" if corpus is not None else "unavailable",
        "features": features,
        **score_features(features),
    }
