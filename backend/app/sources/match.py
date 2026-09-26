"""Column metadata -> CRM property matching with an evidence trail.

Every score is the sum of named contributions (exact name, exact label, HubSpot
export-label dictionary, token overlap, entity namespace, type compatibility,
enum overlap) so a reviewer can see why a column landed on a property.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .models import Candidate, ColumnMapping, ColumnProfile, DerivedObject, SourceSpec

OBJECT_NOUNS = {"company": "companies", "companies": "companies", "contact": "contacts", "contacts": "contacts",
                "person": "contacts", "deal": "deals", "deals": "deals", "opportunity": "deals", "ticket": "tickets", "tickets": "tickets"}
# HubSpot export headers -> internal names (labels the CRM UI shows on exports)
EXPORT_LABELS: dict[str, dict[str, str]] = {
    "contacts": {
        "first name": "firstname", "last name": "lastname", "email": "email", "phone number": "phone",
        "mobile phone number": "mobilephone", "contact owner": "hubspot_owner_id", "job title": "jobtitle",
        "lifecycle stage": "lifecyclestage", "lead status": "hs_lead_status", "create date": "createdate",
        "last activity date": "notes_last_updated", "marketing contact status": "hs_marketable_status",
        "company name": "company", "city": "city", "country/region": "country", "state/region": "state",
        "postal code": "zip", "website url": "website", "linkedin url": "hs_linkedin_url",
    },
    "companies": {
        "company name": "name", "name": "name", "company domain name": "domain", "domain": "domain",
        "company owner": "hubspot_owner_id", "industry": "industry", "city": "city", "country/region": "country",
        "phone number": "phone", "create date": "createdate", "last activity date": "notes_last_updated",
        "number of employees": "numberofemployees", "annual revenue": "annualrevenue", "website url": "website",
    },
    "deals": {
        "deal name": "dealname", "name": "dealname", "amount": "amount", "close date": "closedate",
        "deal stage": "dealstage", "pipeline": "pipeline", "deal owner": "hubspot_owner_id",
        "deal type": "dealtype", "create date": "createdate", "last activity date": "notes_last_updated",
    },
    "tickets": {"ticket name": "subject", "subject": "subject", "ticket owner": "hubspot_owner_id", "priority": "hs_ticket_priority",
                "ticket status": "hs_pipeline_stage", "pipeline": "hs_pipeline", "create date": "createdate"},
}
READ_ONLY = {"hs_object_id", "createdate", "lastmodifieddate", "hs_lastmodifieddate", "notes_last_updated",
             "hs_all_owner_ids", "hs_all_team_ids", "num_associated_contacts", "num_associated_deals", "hs_marketable_status"}
ID_COLUMNS = {"record_id", "id", "hs_object_id", "object_id", "vid"}
ASSOCIATION_PAIRS: dict[tuple[str, str], dict] = {
    ("deals", "companies"): {"primary": (5, 6, "Primary"), "default": (341, 342)},
    ("contacts", "companies"): {"primary": (1, 2, "Primary"), "default": (279, 280)},
    ("deals", "contacts"): {"primary": None, "default": (3, 4)},
    ("tickets", "companies"): {"primary": None, "default": (339, 340)},
    ("tickets", "contacts"): {"primary": None, "default": (16, 15)},
    ("companies", "contacts"): {"primary": (2, 1, "Primary"), "default": (280, 279)},
    ("contacts", "deals"): {"primary": None, "default": (4, 3)},
}
TYPE_COMPAT = {
    "id": {"string", "number"}, "integer": {"number", "string", "enumeration"}, "number": {"number", "string"},
    "datetime": {"datetime", "date", "string"}, "date": {"date", "datetime", "string"}, "email": {"string"},
    "url": {"string"}, "phone": {"string"}, "bool": {"bool", "enumeration", "string"}, "enum": {"enumeration", "string", "bool"},
    "text": {"string", "enumeration"}, "empty": {"string", "number", "datetime", "date", "enumeration", "bool"},
}
PROPOSAL_TYPES = {
    "id": ("string", "text"), "integer": ("number", "number"), "number": ("number", "number"),
    "datetime": ("datetime", "date"), "date": ("date", "date"), "email": ("string", "text"), "url": ("string", "text"),
    "phone": ("string", "text"), "bool": ("bool", "booleancheckbox"), "enum": ("enumeration", "select"),
    "text": ("string", "text"), "empty": ("string", "text"),
}


def norm(text: str) -> str:
    t = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return re.sub(r"_+", "_", t)


def tokens(text: str) -> set[str]:
    return {t for t in norm(text).split("_") if t and t not in {"the", "of", "and"}}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 3)


def slug(entity: str, column: str) -> str:
    return f"{norm(entity)}_{norm(column)}"[:100]


class TargetProperty:
    def __init__(self, raw: dict) -> None:
        self.name: str = raw.get("name") or ""
        self.label: str = raw.get("label") or ""
        self.type: str = raw.get("type") or ""
        self.field_type: str = raw.get("fieldType") or ""
        self.options: set[str] = {str(o.get("value")) for o in (raw.get("options") or []) if o.get("value") is not None}
        self.option_labels: set[str] = {str(o.get("label")).lower() for o in (raw.get("options") or []) if o.get("label")}
        meta = raw.get("modificationMetadata") or {}
        self.read_only: bool = bool(meta.get("readOnlyValue")) or self.name in READ_ONLY
        self.hubspot_defined: bool = bool(raw.get("hubspotDefined"))


def score_column(profile: ColumnProfile, prop: TargetProperty, *, object_type: str, entity: str) -> Candidate | None:
    col_norm, col_tokens = norm(profile.column), tokens(profile.column)
    ev: list[str] = []
    score = 0.0
    if col_norm == norm(prop.name):
        score += 1.0; ev.append("exact name")
    elif col_norm == norm(prop.label):
        score += 0.95; ev.append("exact label")
    elif EXPORT_LABELS.get(object_type, {}).get(profile.column.strip().lower()) == prop.name:
        score += 0.9; ev.append("HubSpot export label")
    else:
        j = max(jaccard(col_tokens, tokens(prop.name)), jaccard(col_tokens, tokens(prop.label)))
        if j >= 0.5:
            score += 0.6 * j; ev.append(f"token overlap {j}")
        ent = norm(entity)
        if prop.name.startswith(ent + "_") and norm(prop.name[len(ent) + 1:]) == col_norm:
            score += 0.9; ev.append("entity namespace + name")
    if score == 0.0:
        return None
    compat = TYPE_COMPAT.get(profile.inferred_type, set())
    if prop.type in compat:
        score += 0.05; ev.append(f"type {profile.inferred_type}~{prop.type}")
    else:
        score -= 0.3; ev.append(f"type conflict {profile.inferred_type} vs {prop.type}")
    if prop.options and profile.values is not None:
        vals = {v for v in profile.values}
        hit = len([v for v in vals if v in prop.options or v.lower() in prop.option_labels])
        if hit == len(vals):
            score += 0.3; ev.append("all values are options")
        elif hit >= len(vals) / 2:
            score += 0.15; ev.append(f"{hit}/{len(vals)} values are options")
        else:
            score -= 0.2; ev.append("values are not options")
    elif prop.options and profile.inferred_type in ("text", "id", "integer") and profile.distinct > 50:
        score -= 0.2; ev.append("free values into an enumeration")
    if prop.read_only:
        ev.append("read-only in HubSpot")
    return Candidate(property=prop.name, label=prop.label, type=prop.type, score=round(min(score, 1.0), 3), evidence=ev, read_only=prop.read_only)


def proposed_property(entity: str, profile: ColumnProfile, object_type: str, source_name: str) -> dict:
    ptype, ftype = PROPOSAL_TYPES.get(profile.inferred_type, ("string", "text"))
    prop = {
        "name": slug(entity, profile.column), "label": f"{entity.upper()} {profile.column}", "type": ptype, "fieldType": ftype,
        "groupName": f"{norm(entity)}information", "description": f"Ad-hoc source '{source_name}', column '{profile.column}' (inferred {profile.inferred_type}, fill {profile.fill_rate:.0%})",
        "objectType": object_type, "status": "proposed",
    }
    if profile.values:
        prop["options"] = [{"label": v, "value": v, "displayOrder": i, "hidden": False} for i, v in enumerate(profile.values)]
    return prop


def transform_for(profile: ColumnProfile, target_type: str | None) -> str | None:
    t = profile.inferred_type
    if target_type in ("datetime",) or (t == "datetime" and target_type is None):
        return "datetime"
    if target_type == "date" or (t == "date" and target_type is None):
        return "date"
    if t in ("integer", "id") and target_type == "number":
        return "int"
    if t == "number" and target_type == "number":
        return "float"
    if t == "bool":
        return "bool"
    if t == "phone":
        return "phone"
    if t == "email":
        return "lower"
    return None


def detect_derived(object_type: str, profiles: list[ColumnProfile], entity: str) -> tuple[list[DerivedObject], set[str]]:
    """Columns naming another object (company_name, legacy_company_id, Primary Associated Company ID...) become a derived object."""
    groups: dict[str, list[ColumnProfile]] = {}
    for p in profiles:
        toks = tokens(p.column)
        for tok in toks:
            other = OBJECT_NOUNS.get(tok)
            if other and other != object_type:
                groups.setdefault(other, []).append(p)
                break
    derived: list[DerivedObject] = []
    consumed: set[str] = set()
    for other, cols in groups.items():
        keys = [p for p in cols if tokens(p.column) & {"id", "key", "ids"}]
        names = [p for p in cols if "name" in tokens(p.column) and p not in keys]
        if not names:  # no column says "name": the first textual, non-identifier column is the display name
            names = [p for p in cols if p not in keys and p.inferred_type in ("text", "enum") and p.distinct > 1]
        keys.sort(key=lambda p: (0 if "id" in tokens(p.column) else 1, -p.distinct))
        key = keys[0] if keys else (names[0] if names else None)
        if key is None:
            continue
        name = names[0] if names and names[0] is not key else None
        columns: dict[str, str] = {}
        for p in cols:
            consumed.add(p.column)
            if p is key or p is name:
                continue
            columns[p.column] = slug(entity, p.column)
        pair = ASSOCIATION_PAIRS.get((object_type, other), {})
        primary = pair.get("primary")
        default = pair.get("default")
        derived.append(
            DerivedObject(
                object=other, key_column=key.column, name_column=name.column if name else None, columns=columns,
                match_by="name" if name else "source_key",
                association_type_id=(primary[0] if primary else (default[0] if default else None)),
                association_reverse_type_id=(primary[1] if primary else (default[1] if default else None)),
                association_label=(primary[2] if primary else None),
                secondary_type_id=(default[0] if primary and default else None),
                secondary_reverse_type_id=(default[1] if primary and default else None),
            )
        )
    return derived, consumed


def match_columns(
    *, entity: str, object_type: str, profiles: list[ColumnProfile], target_props: list[dict], source_name: str,
    key_columns: list[str] | None = None, source_system: str = "",
) -> tuple[list[ColumnMapping], list[DerivedObject], list[str]]:
    props = [TargetProperty(p) for p in target_props if p.get("name")]
    notes: list[str] = []
    derived, consumed = detect_derived(object_type, profiles, entity)
    for d in derived:
        notes.append(f"derived {d.object} from key '{d.key_column}'" + (f" and name '{d.name_column}'" if d.name_column else "") + f" (association type {d.association_type_id})")
    keys = list(key_columns or [])
    mappings: list[ColumnMapping] = []
    ent = norm(entity)
    for profile in profiles:
        col_norm = norm(profile.column)
        m = ColumnMapping(column=profile.column, profile=profile)
        if profile.column in consumed:
            m.role, m.decision = "derived", "auto"
            m.target_object = next(d.object for d in derived if profile.column == d.key_column or profile.column == d.name_column or profile.column in d.columns)
            m.evidence.append("feeds the derived object")
            mappings.append(m)
            continue
        empty = "empty" in profile.flags
        if col_norm in ID_COLUMNS or (col_norm.startswith("hs_") and col_norm.endswith("_id")):
            same_portal = "same_portal" in source_system
            m.role = "source_id" if (same_portal or col_norm.startswith("hs_")) else "key"
            m.match_by = "id" if m.role == "source_id" else None
            m.decision = "ignored" if empty else "auto"
            m.evidence.append("record identifier of the source" if m.role == "key" else "identifier of a record in this portal")
            if empty:
                m.evidence.append("no values in this export; role kept for future exports")
            if profile.column not in keys and m.role == "key" and not empty:
                keys.append(profile.column)
            if "excel_mangled_number" in profile.flags:
                m.evidence.append("some values lost precision in Excel (1.3E+11)")
                notes.append(f"'{profile.column}': values in scientific notation lost precision; re-export as text before loading")
            mappings.append(m)
            continue
        if col_norm.startswith("hs_") and col_norm.replace("hs_", "", 1) in {norm(p.name) for p in props if p.name in ("dealname", "name", "email", "subject")}:
            m.role, m.match_by, m.decision = "lookup", "name", ("ignored" if empty else "review")
            m.property = col_norm.replace("hs_", "", 1)
            m.evidence.append("name of a record in this portal: resolve by name lookup" + ("; no values in this export" if empty else ""))
            mappings.append(m)
            continue
        if empty:
            m.role, m.decision = "ignore", "ignored"
            m.evidence.append("no values in this export")
            mappings.append(m)
            continue
        cands = [c for c in (score_column(profile, p, object_type=object_type, entity=entity) for p in props) if c is not None]
        cands.sort(key=lambda c: -c.score)
        m.candidates = cands[:5]
        best = cands[0] if cands else None
        if best is not None and best.property == "hubspot_owner_id" and profile.inferred_type in ("text", "email", "enum"):
            m.role, m.property, m.match_by = "lookup", "hubspot_owner_id", "owner_email" if profile.inferred_type == "email" else "owner_name"
            m.decision, m.score, m.evidence = "review", best.score, best.evidence + ["values are people, resolve through the owners API"]
            m.proposed_property = proposed_property(entity, profile, object_type, source_name)
            mappings.append(m)
            continue
        if best is not None and best.score >= 0.85 and not best.read_only:
            m.role, m.property, m.decision, m.score, m.evidence = "property", best.property, "auto", best.score, best.evidence
            m.transform = transform_for(profile, best.type)
        elif best is not None and best.score >= 0.5 and not best.read_only:
            m.role, m.property, m.decision, m.score, m.evidence = "property", best.property, "review", best.score, best.evidence
            m.transform = transform_for(profile, best.type)
        else:
            m.role, m.decision = "property", "unmapped"
            m.proposed_property = proposed_property(entity, profile, object_type, source_name)
            m.property = m.proposed_property["name"]
            m.transform = transform_for(profile, m.proposed_property["type"])
            if best is not None and best.read_only:
                m.evidence.append(f"best match {best.property} is read-only in HubSpot; keep the value in the entity namespace")
            m.evidence.append(f"proposed new property {m.property}")
        if "low_fill" in profile.flags:
            m.evidence.append(f"low fill {profile.fill_rate:.1%}")
        if profile.column in keys:
            m.role = "key"
        mappings.append(m)
    if not keys:
        uniq = [p for p in profiles if p.unique and p.inferred_type in ("id", "integer", "text", "email") and p.column not in consumed]
        uniq.sort(key=lambda p: (0 if "id" in tokens(p.column) else 1 if tokens(p.column) & {"key", "uid", "guid", "ref"} else 2, p.index))
        if uniq:
            keys = [uniq[0].column]
            for m in mappings:
                if m.column == keys[0]:
                    m.role = "key"
                    m.evidence.append("chosen as key: unique and filled")
        else:
            notes.append("no unique column found: rows are keyed by their line number, which is not stable between exports")
    by_column = {p.column: p for p in profiles}
    if keys and "excel_mangled_number" in by_column[keys[0]].flags:
        extra = next((p for p in profiles if p.inferred_type == "email" and p.column not in keys), None)
        if extra is not None:
            keys.append(extra.column)
            notes.append(f"composite key '{keys[0]}' + '{extra.column}' because the identifier lost precision in Excel; rows without an email may still collide")
    return mappings, derived, notes, keys


def system_properties(entity: str, object_type: str, source_name: str) -> list[dict]:
    """Properties the loader always writes so the addition stays traceable and idempotent."""
    ent = norm(entity)
    return [
        {"name": f"{ent}_source_key", "label": f"{entity.upper()} source key", "type": "string", "fieldType": "text", "groupName": f"{ent}information",
         "description": "Idempotency key of the ad-hoc source row", "objectType": object_type, "status": "proposed", "system": True},
        {"name": f"{ent}_source_system", "label": f"{entity.upper()} source system", "type": "string", "fieldType": "text", "groupName": f"{ent}information",
         "description": "Which external system the record came from", "objectType": object_type, "status": "proposed", "system": True},
        {"name": "entity_scope", "label": "Entity scope", "type": "enumeration", "fieldType": "select", "groupName": "governance",
         "description": "Business entity the record belongs to (Phase C governance property)", "objectType": object_type, "status": "proposed", "system": True,
         "options": [{"label": e, "value": e} for e in ("WISEKEY", "WISESAT", "SEALSQ", "SEALCOIN", "QUANTUM_AI", "ICALPS", "MIRAEX", "WECAN")]},
    ]


def build_spec(*, name: str, entity: str, object_type: str, file: str, encoding: str, delimiter: str, header_row: int,
               mappings: list[ColumnMapping], derived: list[DerivedObject], keys: list[str], notes: list[str], source_system: str) -> SourceSpec:
    return SourceSpec(
        name=name, entity=entity.upper(), object=object_type, file=file, source_system=source_system, encoding=encoding,
        delimiter=delimiter, header_row=header_row, key_columns=keys, mappings=mappings, derived=derived, notes=notes,
        generated_by=f"hs-logic sources.match {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
    )


def schema_proposal(entity: str, object_type: str, mappings: list[ColumnMapping], derived: list[DerivedObject], source_name: str) -> dict[str, list[dict]]:
    """object -> property definitions to create for this entity (proposed + system)."""
    out: dict[str, list[dict]] = {object_type: []}
    seen: set[tuple[str, str]] = set()

    def add(obj: str, prop: dict) -> None:
        if (obj, prop["name"]) in seen:
            return
        seen.add((obj, prop["name"]))
        out.setdefault(obj, []).append(prop)

    for m in mappings:
        if m.proposed_property:
            add(object_type, m.proposed_property)
    for p in system_properties(entity, object_type, source_name):
        add(object_type, p)
    for d in derived:
        for col, prop_name in d.columns.items():
            add(d.object, {"name": prop_name, "label": f"{entity.upper()} {col}", "type": "string", "fieldType": "text",
                           "groupName": f"{norm(entity)}information", "description": f"Ad-hoc source '{source_name}', column '{col}' on derived {d.object}",
                           "objectType": d.object, "status": "proposed"})
        for p in system_properties(entity, d.object, source_name):
            add(d.object, p)
    return out
