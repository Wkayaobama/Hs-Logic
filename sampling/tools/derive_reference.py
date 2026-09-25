"""Derive model/reference.json (the accuracy stage's known answers) from the
HubSpot-Ruler data assets captured on 2026-09-14:
  Data-Toolkit/hubspot_crm_schema.json   validated_queries, product_distribution_live, pipelines
  SEALSQ/sealsq_property_set.json        meta.sealsq_scope, utilisation_detail
Every reference is a count the backend can answer through
GET /api/export/{object}/count?filter=prop:OP:value (AND-ed filters).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RULER = HERE.parents[2] / "HubSpot-Ruler"
DEFAULT_OUT = HERE.parent / "model" / "reference.json"

SEALSQ_SCOPE_FILTER = {"prop": "pipeline", "op": "IN", "value": "12096408|13772279|766126206|705868909"}
PRODUCT_VALUE_ALIASES = {"ASIC (Undefined)": "Undefined", "VIC409 (QVault 409)": "QVault 409"}


def to_int(text) -> int:
    return int(str(text).replace(",", "").strip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", default=str(RULER / "Data-Toolkit" / "hubspot_crm_schema.json"))
    parser.add_argument("--sealsq", default=str(RULER / "SEALSQ" / "sealsq_property_set.json"))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    schema = json.loads(Path(args.schema).read_text(encoding="utf-8"))
    sealsq = json.loads(Path(args.sealsq).read_text(encoding="utf-8"))
    captured = schema.get("meta", {}).get("captured", "2026-09-14")
    refs: list[dict] = []

    # A reference passes when |actual - expected| <= tolerance_abs OR drift% <= tolerance_pct: the numbers were
    # captured on a given day and the portal keeps growing, so the probe documents drift instead of hiding it.
    # Exact agreement is demanded between the computation paths (backend = extract = direct), not against the capture.
    TOLERANCE = {"fixed": (10, 1.0), "pipeline": (10, 2.0), "product": (5, 2.0), "utilisation": (10, 2.0)}

    def add(rid: str, object_type: str, filters: list[dict], expected: int, source: str, *, group: str, tolerance_pct: float | None = None, label: str = "") -> None:
        abs_tol, pct_tol = TOLERANCE[group]
        refs.append({
            "id": rid, "group": group, "label": label or rid, "object": object_type, "filters": filters,
            "metric": "count", "expected": expected, "tolerance_abs": abs_tol,
            "tolerance_pct": pct_tol if tolerance_pct is None else tolerance_pct, "source": source, "captured": captured,
        })

    scope = sealsq["meta"]["sealsq_scope"]
    add("deals_total", "deals", [], to_int(scope["deals_total"]), "sealsq_property_set.meta.sealsq_scope.deals_total", group="fixed", label="All deals")
    add("deals_sealsq_scope", "deals", [SEALSQ_SCOPE_FILTER], to_int(scope["deals_in_scope"]), "sealsq_property_set.meta.sealsq_scope.deals_in_scope", group="fixed", label="Deals in SEALSQ scope (4 pipelines)")
    labels = {p["label"]: p["id"] for p in schema["pipelines"]}
    for label, count in scope["deals_per_pipeline"].items():
        pid = labels[label]
        add(f"deals_pipeline_{pid}", "deals", [{"prop": "pipeline", "op": "EQ", "value": pid}], to_int(count), "sealsq_property_set.meta.sealsq_scope.deals_per_pipeline", group="pipeline", label=f"Deals in pipeline {label}")
    add("product_qvault_tpm_iot", "deals", [{"prop": "prod___product_name_new_", "op": "EQ", "value": "QVault TPM IOT"}], 32, "hubspot_crm_schema.validated_queries[0]", group="fixed", label="prod___product_name_new_ = 'QVault TPM IOT'")
    add("product_qvault_tpm", "deals", [{"prop": "prod___product_name_new_", "op": "EQ", "value": "QVault TPM"}], 82, "hubspot_crm_schema.validated_queries[0]", group="fixed", label="prod___product_name_new_ = 'QVault TPM'")
    add("wisekey_seal_seal", "deals", [SEALSQ_SCOPE_FILTER, {"prop": "wisekey___seal", "op": "EQ", "value": "Seal"}], 1798, "sealsq_property_set.utilisation_detail.wisekey___seal", group="fixed", label="wisekey___seal = Seal (scope)")
    add("wisekey_seal_wisekey", "deals", [SEALSQ_SCOPE_FILTER, {"prop": "wisekey___seal", "op": "EQ", "value": "Wisekey"}], 22, "sealsq_property_set.utilisation_detail.wisekey___seal", group="fixed", label="wisekey___seal = Wisekey (scope)")
    for row in schema["product_distribution_live"]:
        name, count = row["product"], to_int(row["deals"])
        if name == "Unassigned":
            filters = [{"prop": "prod___product_name_new_", "op": "NOT_HAS_PROPERTY", "value": ""}]
        else:
            filters = [{"prop": "prod___product_name_new_", "op": "EQ", "value": PRODUCT_VALUE_ALIASES.get(name, name)}]
        add(f"product_{name}", "deals", filters, count, "hubspot_crm_schema.product_distribution_live", group="product", tolerance_pct=1.0, label=f"Product {name}")
    for row in sealsq["utilisation_detail"]:
        prop, filled = row["property"], to_int(row["filled"])
        add(f"fill_{prop}", "deals", [SEALSQ_SCOPE_FILTER, {"prop": prop, "op": "HAS_PROPERTY", "value": ""}], filled, "sealsq_property_set.utilisation_detail", group="utilisation", tolerance_pct=1.0, label=f"{prop} filled (scope)")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"captured": captured, "scope_filter": SEALSQ_SCOPE_FILTER, "references": refs}, indent=2, ensure_ascii=False), encoding="utf-8")
    groups = {}
    for r in refs:
        groups[r["group"]] = groups.get(r["group"], 0) + 1
    print(f"wrote {len(refs)} references -> {args.out} {groups}")


if __name__ == "__main__":
    main()
