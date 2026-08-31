"""
Scoring criteria registry — pure data, stdlib-only.

The registry pattern mirrors the CRM card's property registries
(icalpsPropertyConfig.ts): a small typed vocabulary (PredicateKind)
interpreted by app.scoring.engine, so a rule is serializable data.
Editing the scoring model = editing this one file; the invariants are
pinned by backend/tests/test_scoring_engine.py.

Rules reference extracted *features* (see engine.contact_features_from_api),
not raw HubSpot property names, so derived signals like "name" or
"company_count" score the same way from any fetch path.
"""

from dataclasses import dataclass
from enum import Enum


class PredicateKind(str, Enum):
    HAS = "has"  # feature is truthy (non-empty string / non-zero number)
    IN = "in"    # feature value is a member of `value` (frozenset[str])
    GTE = "gte"  # numeric feature >= `value` (int)


@dataclass(frozen=True)
class Rule:
    id: str      # stable slug — Phase 2 write-back keys on it; never rename casually
    label: str   # human label for breakdowns / dashboard legend
    feature: str  # key in the extracted feature dict, NOT the raw HubSpot property
    kind: PredicateKind
    points: int
    value: frozenset[str] | int | None = None  # IN membership set or GTE threshold


@dataclass(frozen=True)
class Band:
    label: str        # lowercase, stable — becomes the CRM enum value in Phase 2
    min_points: int   # inclusive lower bound; bands are evaluated top-down


# ── EXCLUDED — synthetic IcAlps identifiers ─────────────────────────────────
# icalps_contact_id / icalps_company_id are card-origin provenance markers
# (assigned by the IcAlps CRM card's create functions), NOT record quality.
# Their presence must never be a scoring criterion and they must never appear
# in a fetch list. Enforced by test_registry_excludes_synthetic_ids.
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

# What the API layer must fetch for feature extraction — the registry drives
# the fetch, the way the card registry drives FETCH_KEYS in IcAlpsCard.tsx.
# phone has no rule yet: it rides along as an extracted feature (single-record
# debug payload, and the likeliest next reachability criterion).
CONTACT_PROPERTIES: tuple[str, ...] = (
    "email",
    "firstname",
    "lastname",
    "phone",
    "lifecyclestage",
)

# Arbitrary static criteria — points are a starting convention (sum = 100).
# Congruent with the health scan's completeness bar: email + name + company
# sum to 75 (hot). Deliberately STRICTER than the health classifier's
# lifecycle shortcut — a lifecycle-only contact counts as MQL on the health
# tab but earns just the 25-point bonus here (banded cold).
CONTACT_RULES: tuple[Rule, ...] = (
    Rule(id="has_email", label="Has email address",
         feature="email", kind=PredicateKind.HAS, points=30),
    Rule(id="has_name", label="Has first or last name",
         feature="name", kind=PredicateKind.HAS, points=20),
    Rule(id="has_company", label="Associated to a company",
         feature="company_count", kind=PredicateKind.GTE, points=25, value=1),
    Rule(id="lifecycle_bonus", label="Lifecycle is MQL or beyond",
         feature="lifecycle", kind=PredicateKind.IN, points=25,
         value=MQL_LIFECYCLES),
)

# Evaluated top-down, first match wins; the last band must have min_points=0.
CONTACT_BANDS: tuple[Band, ...] = (
    Band(label="hot", min_points=75),
    Band(label="warm", min_points=45),
    Band(label="cold", min_points=0),
)

CONTACT_MAX_SCORE: int = sum(r.points for r in CONTACT_RULES)

# Deal scoring: same shapes, populated when a deal model is defined
# (e.g. amount GTE, icalps_stage IN, target close date HAS).
DEAL_PROPERTIES: tuple[str, ...] = ()
DEAL_RULES: tuple[Rule, ...] = ()
DEAL_BANDS: tuple[Band, ...] = ()
