"""Derive model/cardinality.csv from HubSpot-Ruler/ontology_workbook_full.xlsx.

The workbook (sheets 'Entity-Pair Inventory', 'Relationship Lineage',
'Metagraph - Edges') is the source; this script fixes the direction of the
association type ids with HubSpot's canonical table and writes the rule set
stage 04 (cascade validation) applies. Run once, commit the CSV, re-run when
the workbook changes.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from _xlsx import read_sheets

HERE = Path(__file__).resolve().parent
DEFAULT_WORKBOOK = HERE.parents[2] / "HubSpot-Ruler" / "ontology_workbook_full.xlsx"
DEFAULT_OUT = HERE.parent / "model" / "cardinality.csv"

# HubSpot canonical forward type ids (from source object to target object).
FORWARD_TYPES = {
    ("deal", "company"): {"default": 341, "primary": 5},
    ("company", "deal"): {"default": 342, "primary": 6},
    ("deal", "contact"): {"default": 3, "labels": {"Champion": 247}},
    ("contact", "deal"): {"default": 4, "labels": {"Champion": 248}},
    ("contact", "company"): {"default": 279, "primary": 1, "labels": {"IcAlps_PrimaryContact": 271}},
    ("company", "contact"): {"default": 280, "primary": 2, "labels": {"IcAlps_PrimaryContact": 272}},
    ("note", "company"): {"default": 190}, ("note", "contact"): {"default": 202}, ("note", "deal"): {"default": 214},
    ("call", "company"): {"default": 182}, ("call", "contact"): {"default": 194},
    ("meeting", "company"): {"default": 188}, ("meeting", "contact"): {"default": 200},
    ("task", "company"): {"default": 192}, ("task", "contact"): {"default": 204},
    ("ticket", "company"): {"default": 339}, ("ticket", "contact"): {"default": 16}, ("ticket", "deal"): {"default": 28},
}
PLURAL = {"deal": "deals", "company": "companies", "contact": "contacts", "note": "notes", "call": "calls",
          "meeting": "meetings", "task": "tasks", "ticket": "tickets"}
ENGAGEMENTS = {"note", "call", "meeting", "task"}


def pct(text: str) -> float | None:
    text = (text or "").strip().rstrip("%")
    try:
        return round(float(text) / 100.0, 4)
    except ValueError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbook", default=str(DEFAULT_WORKBOOK))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    sheets = read_sheets(args.workbook, {"Relationship Lineage"})
    rows = sheets["Relationship Lineage"]
    header, body = rows[0], [r for r in rows[1:] if r and r[0]]
    idx = {name: i for i, name in enumerate(header)}

    def col(r: list[str], name: str) -> str:
        i = idx.get(name)
        return r[i] if i is not None and i < len(r) else ""

    out_rows = []
    for r in body:
        edge, source, target, cardinality = col(r, "relationship"), col(r, "source_entity"), col(r, "target_entity"), col(r, "cardinality")
        types = FORWARD_TYPES.get((source, target), {})
        expected_rate = pct(col(r, "source_side_fill_rate"))
        primary_type = ""
        max_primary = ""
        min_per_source = 0
        max_per_source = ""
        if edge == "deal_primary_company":
            primary_type, max_primary, min_per_source = types["primary"], 1, 1
        elif edge == "deal_primary_contact":
            min_per_source = 1
        elif edge == "contact_primary_company":
            primary_type, max_primary, min_per_source = types["primary"], 1, 1
        elif edge == "company_icalps_primary_contact":
            primary_type, max_primary, max_per_source = types["labels"]["IcAlps_PrimaryContact"], 1, 1
        type_ids = [types.get("default")] + ([types["primary"]] if "primary" in types else []) + list(types.get("labels", {}).values())
        if edge == "company_icalps_primary_contact":
            # the 1:1 rule is about the IcAlps_PrimaryContact label only, not every company->contact link
            type_ids = [types["labels"]["IcAlps_PrimaryContact"]]
        out_rows.append(
            {
                "edge_name": edge,
                "source": PLURAL.get(source, source),
                "target": PLURAL.get(target, target),
                "cardinality": cardinality,
                "source_type_ids": "|".join(str(t) for t in type_ids if t is not None),
                "primary_type_id": primary_type,
                "min_per_source": min_per_source,
                "max_primary_per_source": max_primary,
                "max_per_source": max_per_source,
                "expected_rate": "" if expected_rate is None else expected_rate,
                "orphan_group": "engagement" if source in ENGAGEMENTS else "",
                "workbook_expected": col(r, "expected"),
                "workbook_resolvable": col(r, "resolvable"),
                "note": col(r, "notes"),
            }
        )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)
    print(f"wrote {len(out_rows)} edges -> {args.out}")


if __name__ == "__main__":
    main()
