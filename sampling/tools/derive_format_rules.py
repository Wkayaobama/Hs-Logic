"""Derive model/format_rules.csv (stage 03 enum/format expectations) from
HubSpot-Ruler/Data-Toolkit/hubspot_crm_schema.json enum_values plus a few
generic format rules. Columns: object,property,rule_type,spec,source.
  rule_type enum   -> spec is 'a|b|c' (allowed values, '|' separated)
  rule_type regex  -> spec is a .NET/PCRE-compatible regex the value must match
  rule_type type   -> spec is number|date|datetime|bool
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RULER = HERE.parents[2] / "HubSpot-Ruler"
DEFAULT_OUT = HERE.parent / "model" / "format_rules.csv"
OBJECT_NAMES = {"DEAL": "deals", "COMPANY": "companies", "CONTACT": "contacts", "TICKET": "tickets"}
# Enum lists that are volatile or only partially captured in the schema asset (owner ids, HubSpot's
# full industry list): checking them produces noise, not data-quality findings.
ENUM_DENYLIST = {"hubspot_owner_id", "hubspot_team_id", "hs_all_owner_ids", "hs_all_team_ids", "industry",
                 "wisekey_sales_owner", "source"}
GENERIC = [
    ("contacts", "email", "regex", r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    ("companies", "domain", "regex", r"^(?!https?://)(?!www\.)[a-z0-9.-]+\.[a-z]{2,}$"),
    ("deals", "amount", "type", "number"),
    ("deals", "amount_in_home_currency", "type", "number"),
    ("deals", "closedate", "type", "datetime"),
    ("deals", "createdate", "type", "datetime"),
    ("deals", "hs_lastmodifieddate", "type", "datetime"),
    ("contacts", "createdate", "type", "datetime"),
    ("contacts", "lastmodifieddate", "type", "datetime"),
    ("companies", "createdate", "type", "datetime"),
    ("companies", "hs_lastmodifieddate", "type", "datetime"),
    ("tickets", "createdate", "type", "datetime"),
    ("deals", "hs_is_closed", "type", "bool"),
    ("deals", "hs_is_closed_won", "type", "bool"),
    ("tickets", "is_icalps", "type", "bool"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", default=str(RULER / "Data-Toolkit" / "hubspot_crm_schema.json"))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    schema = json.loads(Path(args.schema).read_text(encoding="utf-8"))
    rows: list[dict] = []
    for obj_key, object_type in OBJECT_NAMES.items():
        for prop in schema["objects"].get(obj_key, []):
            values = prop.get("enum_values") or []
            if not values or not prop.get("name") or prop["name"] in ENUM_DENYLIST:
                continue
            vals = [str(v.get("value") if isinstance(v, dict) else v) for v in values]
            vals = [v for v in vals if v != ""]
            if not vals:
                continue
            rows.append({"object": object_type, "property": prop["name"], "rule_type": "enum", "spec": "|".join(dict.fromkeys(vals)), "source": f"hubspot_crm_schema.objects.{obj_key}.enum_values"})
    for object_type, prop, rule_type, spec in GENERIC:
        rows.append({"object": object_type, "property": prop, "rule_type": rule_type, "spec": spec, "source": "generic"})
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["object", "property", "rule_type", "spec", "source"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rules -> {args.out}")


if __name__ == "__main__":
    main()
