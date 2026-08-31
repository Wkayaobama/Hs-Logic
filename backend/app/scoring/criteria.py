"""
Scoring criteria registry — pure data, stdlib-only.

The registry pattern mirrors the CRM card's property registries
(icalpsPropertyConfig.ts): a small typed vocabulary (PredicateKind)
interpreted by app.scoring.engine, so a rule is serializable data.
Editing the scoring model = editing this one file; the invariants are
pinned by backend/tests/test_scoring_engine.py and enforced at import
time by validate_registry() at the bottom of this module.

Rules reference extracted *features* (see engine.contact_features_from_api),
not raw HubSpot property names, so derived signals like "name",
"email_domain" or "days_since_last_visit" score the same way from any
fetch path.

Tier model (2026-08-31, probed against portal 9201667):
  Tier 1  — CRM-native identity signals (email, corporate domain, name,
            company, lifecycle, role/country/source fit).
  Tier 1b — corpus-level signals computed across the whole scan
            (duplicate-cluster membership).
  Tier 2  — web-analytics signals (sessions, recency, key pages) —
            populated for ~33% of contacts; absence simply scores 0.
  Tier 3  — external intent (net-new visitors, intent strength) is
            deliberately EXCLUDED until an intent source exists.
"""

from dataclasses import dataclass
from enum import Enum


class PredicateKind(str, Enum):
    HAS = "has"                      # feature is truthy (non-empty string / non-zero number)
    IN = "in"                        # feature value is a member of `value` (frozenset[str])
    NOT_IN = "not_in"                # feature is truthy AND not a member of `value`
    GTE = "gte"                      # numeric feature >= `value` (int)
    CONTAINS_ANY = "contains_any"    # text feature contains any substring in `value` (URLs)
    HAS_ANY_TOKEN = "has_any_token"  # any word token of the text feature is in `value`
    WITHIN_DAYS = "within_days"      # numeric day-count feature is 0..`value` (int)


@dataclass(frozen=True)
class Rule:
    id: str      # stable slug — the write-back phase keys on it; never rename casually
    label: str   # human label for breakdowns / dashboard legend
    feature: str  # key in the extracted feature dict, NOT the raw HubSpot property
    kind: PredicateKind
    points: int
    value: frozenset[str] | int | None = None  # membership/keyword set or numeric threshold
    # Tri-state features (True/False/None-unknown, e.g. corpus signals):
    # when the feature is None the rule is skipped entirely — excluded from
    # score AND max_score — so "unknown" is never scored as a miss.
    skip_when_missing: bool = False


@dataclass(frozen=True)
class Band:
    label: str        # lowercase, stable — becomes the CRM enum value at write-back
    min_points: int   # inclusive lower bound; bands are evaluated top-down


@dataclass(frozen=True)
class Gate:
    """Hard disqualifier: fires when ALL listed features are falsy."""

    id: str
    label: str
    empty_features: tuple[str, ...]


# ── BINDING RULE — the icalps_ namespace is excluded from scoring ───────────
# Every icalps_-prefixed property is operator/business data or card-origin
# provenance (the synthetic ids), never a quality signal. NO icalps_ property
# may be fetched, feature-extracted, or rule-referenced by the scoring
# script. Enforced everywhere: validate_registry() below fails the import,
# tests pin it, and the probe property lists pass through the same check.
EXCLUDED_PREFIX: str = "icalps_"
# Kept for doc continuity — the two synthetic ids that seeded the rule.
EXCLUDED_PROPERTIES: frozenset[str] = frozenset({
    "icalps_contact_id",
    "icalps_company_id",
})

# Lifecycle stages HubSpot already treats as marketing-qualified or beyond.
# Single source for both the health scan (routes/hubspot.py re-imports it)
# and the lifecycle_bonus rule below. Values are HubSpot internal ids.
MQL_LIFECYCLES: frozenset[str] = frozenset({
    "marketingqualifiedlead",
    "salesqualifiedlead",
    "opportunity",
    "customer",
    "evangelist",
})

# ── Fit registries ──────────────────────────────────────────────────────────
# FREEMAIL_DOMAINS is universal knowledge — seeded now. The others are
# portal-semantic choices DEFERRED until after probing (/api/scoring/probe
# aggregates the portal's actual jobtitles, countries, sources, industries
# and URL paths to pick from). An empty registry makes its rule INACTIVE:
# excluded from scoring and from max_score, echoed with configured=false.
FREEMAIL_DOMAINS: frozenset[str] = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.fr", "yahoo.co.uk",
    "hotmail.com", "hotmail.fr", "outlook.com", "outlook.fr", "live.com",
    "live.fr", "msn.com", "icloud.com", "me.com", "aol.com", "gmx.com",
    "gmx.de", "gmx.net", "web.de", "proton.me", "protonmail.com",
    "orange.fr", "wanadoo.fr", "free.fr", "sfr.fr", "laposte.net",
    "bluewin.ch", "example.com",
})

ROLE_FIT_KEYWORDS: frozenset[str] = frozenset()   # PENDING PROBE (jobtitle tokens)
TARGET_COUNTRIES: frozenset[str] = frozenset()    # PENDING PROBE (lowercased values)
SOURCE_QUALITY: frozenset[str] = frozenset()      # PENDING PROBE (UPPER_SNAKE values)
PAGE_CATEGORIES: frozenset[str] = frozenset()     # PENDING PROBE (url path fragments)
TARGET_INDUSTRIES: frozenset[str] = frozenset()   # PENDING PROBE (company industry enum values)

# ── Fetch lists ─────────────────────────────────────────────────────────────
# The registry drives the fetch, the way the card registry drives FETCH_KEYS
# in IcAlpsCard.tsx. All names verified native on portal 9201667; none may
# carry the excluded prefix (validate_registry enforces it).
CONTACT_PROPERTIES: tuple[str, ...] = (
    "email",
    "firstname",
    "lastname",
    "phone",
    "lifecyclestage",
    "jobtitle",
    "country",
    "ip_country",
    "hs_email_domain",
    "hs_analytics_source",
    "hs_latest_source",
    "hs_analytics_num_visits",
    "hs_analytics_last_url",
    "hs_analytics_last_visit_timestamp",
)

# Probe fetch lists (routes/scoring.py /probe) — distributions only.
PROBE_CONTACT_PROPERTIES: tuple[str, ...] = (
    "email",
    "jobtitle",
    "country",
    "ip_country",
    "hs_email_domain",
    "hs_analytics_source",
    "hs_latest_source",
    "hs_analytics_last_url",
)
PROBE_COMPANY_PROPERTIES: tuple[str, ...] = (
    "domain",
    "industry",
)

# ── Rules ───────────────────────────────────────────────────────────────────
# Points are a starting convention: the FULL model sums to 100 ("score
# threshold 1-100"); while fit registries await probing, max_score reflects
# only the ACTIVE rules (80 today) — /criteria echoes both.
# Congruence note: deliberately STRICTER than the health classifier's
# lifecycle shortcut — a lifecycle-only contact earns just the bonus here.
CONTACT_RULES: tuple[Rule, ...] = (
    # Tier 1 — CRM identity
    Rule(id="has_email", label="Has email address",
         feature="email", kind=PredicateKind.HAS, points=15),
    Rule(id="corporate_email", label="Corporate email domain (not freemail)",
         feature="email_domain", kind=PredicateKind.NOT_IN, points=10,
         value=FREEMAIL_DOMAINS),
    Rule(id="has_name", label="Has first or last name",
         feature="name", kind=PredicateKind.HAS, points=10),
    Rule(id="has_company", label="Associated to a company",
         feature="company_count", kind=PredicateKind.GTE, points=10, value=1),
    Rule(id="lifecycle_bonus", label="Lifecycle is MQL or beyond",
         feature="lifecycle", kind=PredicateKind.IN, points=10,
         value=MQL_LIFECYCLES),
    Rule(id="role_fit", label="Job title matches target roles",
         feature="jobtitle", kind=PredicateKind.HAS_ANY_TOKEN, points=8,
         value=ROLE_FIT_KEYWORDS),
    Rule(id="country_fit", label="Country in target markets",
         feature="country", kind=PredicateKind.IN, points=4,
         value=TARGET_COUNTRIES),
    Rule(id="source_quality", label="Acquisition source is high-quality",
         feature="source", kind=PredicateKind.IN, points=4,
         value=SOURCE_QUALITY),
    # Tier 1b — corpus signals (tri-state: unknown outside a batch scan)
    Rule(id="unique_record", label="Not in a duplicate cluster",
         feature="is_unique", kind=PredicateKind.HAS, points=5,
         skip_when_missing=True),
    # Tier 2 — web analytics
    Rule(id="visited_website", label="Has visited the website",
         feature="visit_count", kind=PredicateKind.GTE, points=5, value=1),
    Rule(id="engaged_visitor", label="3+ website sessions",
         feature="visit_count", kind=PredicateKind.GTE, points=5, value=3),
    Rule(id="recent_visit", label="Visited within the last 30 days",
         feature="days_since_last_visit", kind=PredicateKind.WITHIN_DAYS,
         points=10, value=30),
    Rule(id="key_page_visited", label="Last page seen is a key page",
         feature="last_url", kind=PredicateKind.CONTAINS_ANY, points=4,
         value=PAGE_CATEGORIES),
)

# Evaluated top-down, first match wins; the last band must have min_points=0.
CONTACT_BANDS: tuple[Band, ...] = (
    Band(label="hot", min_points=75),
    Band(label="warm", min_points=45),
    Band(label="cold", min_points=0),
)

# Relevance normalization (MQL/NQL): gates override points entirely —
# an unreachable or anonymous record is NQL no matter what it scored
# (mirrors the health scan's NQL semantics). Otherwise the threshold
# splits MQL from borderline.
DISQUALIFIERS: tuple[Gate, ...] = (
    Gate(id="unreachable", label="No email and no phone",
         empty_features=("email", "phone")),
    Gate(id="anonymous", label="No first or last name",
         empty_features=("name",)),
)
MQL_THRESHOLD: int = 45

CONTACT_FULL_MODEL_SCORE: int = sum(r.points for r in CONTACT_RULES)

# Deal scoring: same shapes, populated when a deal model is defined.
DEAL_PROPERTIES: tuple[str, ...] = ()
DEAL_RULES: tuple[Rule, ...] = ()
DEAL_BANDS: tuple[Band, ...] = ()


# ── Import-time enforcement ─────────────────────────────────────────────────


def validate_registry(properties, rules) -> None:
    """Raise ValueError on any icalps_-namespace leak into scoring inputs.

    Called at import for every fetch list and rule set, so a violating edit
    breaks the app (and the test suite) immediately — the binding rule is
    enforced by the module itself, not by convention.
    """
    for prop in properties:
        if prop.startswith(EXCLUDED_PREFIX) or prop in EXCLUDED_PROPERTIES:
            raise ValueError(
                f"binding rule violation: property '{prop}' is in the excluded "
                f"'{EXCLUDED_PREFIX}' namespace and cannot be a scoring input"
            )
    for rule in rules:
        if rule.feature.startswith(EXCLUDED_PREFIX):
            raise ValueError(
                f"binding rule violation: rule '{rule.id}' feature "
                f"'{rule.feature}' is in the excluded '{EXCLUDED_PREFIX}' namespace"
            )


validate_registry(CONTACT_PROPERTIES, CONTACT_RULES)
validate_registry(PROBE_CONTACT_PROPERTIES, ())
validate_registry(PROBE_COMPANY_PROPERTIES, ())
validate_registry(DEAL_PROPERTIES, DEAL_RULES)
