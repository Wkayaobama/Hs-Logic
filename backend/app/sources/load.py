"""Apply a SourceSpec: rows -> export-contract records (main + derived objects), JSONL for the probe,
an import plan for HubSpot batch endpoints, an optional ingest into the fake CRM, and a load report."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.hubspot.client import HubSpotClient, RequestStats, get_client

from .match import ASSOCIATION_PAIRS, norm, schema_proposal, system_properties
from .models import SourceSpec
from .profile import read_rows
from .records import RecordIndex, build_index

DATE_FORMATS = ["%Y-%m-%d %H:%M:%S.%f UTC", "%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%d/%m/%Y %H:%M", "%d/%m/%Y", "%d.%m.%Y %H:%M", "%d.%m.%Y"]


def to_datetime(value: str) -> str | None:
    v = value.strip()
    if not v:
        return None
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(v, fmt).replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except ValueError:
            continue
    if re.fullmatch(r"\d{12,13}", v):
        return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return None


def transform(value: str, kind: str | None) -> tuple[str | None, str | None]:
    """Returns (value, issue)."""
    if value is None or value == "":
        return None, None
    if kind == "datetime":
        out = to_datetime(value)
        return (out, None) if out else (value, "unparsed datetime")
    if kind == "date":
        out = to_datetime(value)
        return (out[:10], None) if out else (value, "unparsed date")
    if kind == "int":
        v = value.replace(" ", "").replace("'", "")
        if re.fullmatch(r"-?\d+", v):
            return v, None
        if re.fullmatch(r"-?\d(\.\d+)?E\+\d+", v):
            return str(int(float(v))), "excel precision lost"
        try:
            return str(int(float(v.replace(",", ".")))), None
        except ValueError:
            return value, "not a number"
    if kind == "float":
        try:
            return str(float(value.replace(",", ".").replace(" ", ""))), None
        except ValueError:
            return value, "not a number"
    if kind == "bool":
        return ("true" if value.lower() in ("true", "yes", "oui", "1") else "false"), None
    if kind == "phone":
        return value.lstrip("'").strip(), None
    if kind == "lower":
        return value.lower().strip(), None
    return value, None


def synthetic_id(entity: str, object_type: str, key: str) -> str:
    return f"src-{norm(entity)}-{object_type}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"


async def load_source(
    spec: SourceSpec, *, base_dir: Path, out_dir: Path, run_id: str | None = None, match_records: bool = True,
    ingest_fake: bool = False, limit: int | None = None, client: HubSpotClient | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    stats = RequestStats()
    entity = spec.entity.upper()
    ent = norm(entity)
    path = (base_dir / spec.file) if not Path(spec.file).is_absolute() else Path(spec.file)
    header, rows = read_rows(path, encoding=spec.encoding, delimiter=spec.delimiter, header_row=spec.header_row, limit=limit)
    report: dict[str, Any] = {"entity": entity, "object": spec.object, "source": spec.name, "file": str(path), "rows": len(rows),
                              "issues": {}, "skipped_no_key": 0, "duplicate_keys": 0, "matched_existing": {}, "unresolved": {}}

    def issue(name: str) -> None:
        report["issues"][name] = report["issues"].get(name, 0) + 1

    client = client or get_client()
    indexes: dict[str, RecordIndex] = {}

    async def index_for(object_type: str) -> RecordIndex | None:
        if not match_records:
            return None
        if object_type not in indexes:
            indexes[object_type] = await build_index(client, object_type, entity, max_records=settings.record_index_max, stats=stats)
        return indexes[object_type]

    # ── derived objects first (companies inside a deal export, ...) ──────────
    derived_records: dict[str, dict[str, dict]] = {}   # object -> key -> record
    derived_ids: dict[str, dict[str, str]] = {}        # object -> key -> id
    for d in spec.derived:
        recs: dict[str, dict] = {}
        ids: dict[str, str] = {}
        idx = await index_for(d.object)
        for row in rows:
            key = row.get(d.key_column, "").strip()
            if not key:
                continue
            if key in recs:
                continue
            props: dict[str, Any] = {f"{ent}_source_key": key, f"{ent}_source_system": spec.source_system or spec.name, "entity_scope": entity}
            name_value = row.get(d.name_column, "").strip() if d.name_column else ""
            if d.object == "companies" and name_value:
                props["name"] = name_value
            elif d.object == "contacts" and name_value:
                props["email" if "@" in name_value else "lastname"] = name_value
            elif name_value:
                props["dealname" if d.object == "deals" else "subject"] = name_value
            for col, prop in d.columns.items():
                v = row.get(col, "").strip()
                if v:
                    props[prop] = v
            rid, score, how = None, 0.0, "not matched"
            if idx is not None:
                rid, score, how = idx.match("source_key", key)
                if rid is None and name_value:
                    rid, score, how = idx.match(d.match_by, name_value)
            existing = rid is not None
            if not existing:
                rid = synthetic_id(entity, d.object, key)
            recs[key] = {"id": rid, "created_at": None, "updated_at": None, "archived": False, "properties": props,
                         "associations": {}, "_existing": existing, "_match": how, "_score": score}
            ids[key] = rid
            if existing:
                report["matched_existing"][d.object] = report["matched_existing"].get(d.object, 0) + 1
        derived_records[d.object] = recs
        derived_ids[d.object] = ids

    # ── main records ──────────────────────────────────────────────────────────
    main_idx = await index_for(spec.object)
    main: dict[str, dict] = {}
    mappings = {m.column: m for m in spec.mappings}
    for line_no, row in enumerate(rows, start=1):
        key = "|".join(row.get(c, "").strip() for c in spec.key_columns) if spec.key_columns else f"line-{line_no}"
        if spec.key_columns and not key.strip("|"):
            report["skipped_no_key"] += 1
            continue
        if key in main:
            report["duplicate_keys"] += 1
        props: dict[str, Any] = dict(spec.defaults)
        props.update({f"{ent}_source_key": key, f"{ent}_source_system": spec.source_system or spec.name, "entity_scope": entity})
        rid: str | None = None
        existing = False
        how = ""
        assoc: dict[str, list[dict]] = {}
        for col, m in mappings.items():
            raw = row.get(col, "")
            if m.role in ("ignore", "derived") or m.decision == "ignored":
                continue
            if m.role == "key":
                if m.property and m.property != f"{ent}_source_key":
                    props[m.property] = raw or None
                continue
            if m.role == "source_id":
                if raw and main_idx is not None:
                    rid, _, how = main_idx.match("id", raw)
                    existing = rid is not None
                    if not existing:
                        issue("source_id not found")
                continue
            if m.role == "lookup":
                if raw:
                    if m.property == "hubspot_owner_id":
                        props[f"{ent}_{norm(col)}"] = raw
                        issue("owner needs resolution")
                    elif main_idx is not None and m.match_by:
                        rid2, _, how2 = main_idx.match(m.match_by, raw)
                        if rid2 and rid is None:
                            rid, existing, how = rid2, True, how2
                continue
            if m.role == "association":
                if raw and m.target_object:
                    tidx = await index_for(m.target_object)
                    tid, _, thow = (tidx.match(m.match_by or "name", raw) if tidx is not None else (None, 0.0, "no index"))
                    if tid:
                        pair = ASSOCIATION_PAIRS.get((spec.object, m.target_object), {})
                        prim, default = pair.get("primary"), pair.get("default")
                        links = assoc.setdefault(m.target_object, [])
                        if prim:
                            links.append({"id": tid, "typeId": prim[0], "label": prim[2], "category": "HUBSPOT_DEFINED"})
                        if default:
                            links.append({"id": tid, "typeId": default[0], "label": None, "category": "HUBSPOT_DEFINED"})
                    else:
                        report["unresolved"][col] = report["unresolved"].get(col, 0) + 1
                continue
            if not m.property:
                continue
            value, problem = transform(raw, m.transform)
            if problem:
                issue(f"{col}: {problem}")
            if value is not None:
                props[m.property] = value
        # derived associations
        for d in spec.derived:
            dkey = row.get(d.key_column, "").strip()
            if not dkey or dkey not in derived_ids.get(d.object, {}):
                continue
            did = derived_ids[d.object][dkey]
            links = assoc.setdefault(d.object, [])
            if d.association_type_id:
                links.append({"id": did, "typeId": d.association_type_id, "label": d.association_label, "category": "USER_DEFINED" if d.association_label and d.association_label not in ("Primary",) else "HUBSPOT_DEFINED"})
            if d.secondary_type_id:
                links.append({"id": did, "typeId": d.secondary_type_id, "label": None, "category": "HUBSPOT_DEFINED"})
        if rid is None:
            rid = synthetic_id(entity, spec.object, key)
        elif existing:
            report["matched_existing"][spec.object] = report["matched_existing"].get(spec.object, 0) + 1
        created = props.get("createdate")
        main[key] = {"id": rid, "created_at": created, "updated_at": props.get("hs_lastmodifieddate") or props.get("lastmodifieddate"),
                     "archived": False, "properties": props, "associations": assoc, "_existing": existing, "_match": how}

    # reverse links on derived records so cardinality checks see both sides
    for d in spec.derived:
        pair_rev = d.association_reverse_type_id
        sec_rev = d.secondary_reverse_type_id
        for key, rec in main.items():
            for link in rec["associations"].get(d.object, []):
                target = next((r for r in derived_records[d.object].values() if r["id"] == link["id"]), None)
                if target is None:
                    continue
                back = target["associations"].setdefault(spec.object, [])
                if link["typeId"] == d.association_type_id and pair_rev:
                    back.append({"id": rec["id"], "typeId": pair_rev, "label": d.association_label, "category": link["category"]})
                elif link["typeId"] == d.secondary_type_id and sec_rev:
                    back.append({"id": rec["id"], "typeId": sec_rev, "label": None, "category": "HUBSPOT_DEFINED"})

    # ── outputs ──────────────────────────────────────────────────────────────
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    counts: dict[str, int] = {}
    objects_manifest: list[dict] = []

    def write_jsonl(object_type: str, records: list[dict]) -> None:
        p = out_dir / f"{object_type}.jsonl"
        with open(p, "w", encoding="utf-8") as fh:
            for rec in records:
                clean = {k: v for k, v in rec.items() if not k.startswith("_")}
                fh.write(json.dumps(clean, ensure_ascii=False) + "\n")
        files[object_type] = str(p)
        counts[object_type] = len(records)
        manifest = {"object": object_type, "business_entity": entity, "run_id": run_id, "scope_mode": "file", "source": spec.name,
                    "no_marker": False, "records": len(records), "pages": 1, "capped": False, "sample_size": limit or 0,
                    "hs_requests": 0, "hs_429": 0, "hs_retries": 0, "backend_ms": 0, "duration_ms": 0, "records_per_s": 0,
                    "file": str(p), "extracted_at": datetime.now(timezone.utc).isoformat()}
        objects_manifest.append(manifest)
        (out_dir / f"{object_type}.manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    write_jsonl(spec.object, list(main.values()))
    for object_type, recs in derived_records.items():
        write_jsonl(object_type, list(recs.values()))

    # import plan: HubSpot batch payloads keyed by source key (ids are resolved at apply time by mir-load / Phase D)
    def plan_for(object_type: str, records: list[dict]) -> dict:
        create = [{"source_key": r["properties"].get(f"{ent}_source_key"), "properties": {k: v for k, v in r["properties"].items() if v is not None}}
                  for r in records if not r["_existing"]]
        update = [{"id": r["id"], "source_key": r["properties"].get(f"{ent}_source_key"), "properties": {k: v for k, v in r["properties"].items() if v is not None}}
                  for r in records if r["_existing"]]
        return {"object": object_type, "create": {"endpoint": f"/crm/v3/objects/{object_type}/batch/create", "batch_size": 100, "inputs": create},
                "update": {"endpoint": f"/crm/v3/objects/{object_type}/batch/update", "batch_size": 100, "inputs": update}}

    associations_plan = []
    for rec in main.values():
        for to_object, links in rec["associations"].items():
            for link in links:
                associations_plan.append({"from_object": spec.object, "from_id": rec["id"], "from_source_key": rec["properties"].get(f"{ent}_source_key"),
                                          "to_object": to_object, "to_id": link["id"], "type_id": link["typeId"], "label": link["label"]})
    plan = {
        "entity": entity, "source": spec.name, "generated_at": datetime.now(timezone.utc).isoformat(), "status": "plan-only (no production writes)",
        "order": list(derived_records.keys()) + [spec.object],
        "properties_to_create": schema_proposal(entity, spec.object, spec.mappings, spec.derived, spec.name),
        "batches": {**{o: plan_for(o, list(r.values())) for o, r in derived_records.items()}, spec.object: plan_for(spec.object, list(main.values()))},
        "associations": {"endpoint": "/crm/v4/associations/{from}/{to}/batch/create", "inputs": associations_plan},
    }
    (out_dir / "import_plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    files["import_plan"] = str(out_dir / "import_plan.json")

    ingested = None
    if ingest_fake:
        ingested = await ingest_into_fake(spec, main, derived_records, plan["properties_to_create"], client=client)
        report["ingested"] = ingested

    duration_ms = int((time.perf_counter() - started) * 1000)
    merged = {"run_id": run_id, "business_entity": entity, "server": None, "sample_size": limit or 0, "source": "file", "objects": objects_manifest,
              "records": sum(counts.values()), "hs_requests": stats.hs_requests, "hs_429": stats.hs_429, "hs_retries": stats.retries,
              "capped": False, "duration_ms": duration_ms, "extracted_at": datetime.now(timezone.utc).isoformat()}
    (out_dir / "manifest.json").write_text(json.dumps(merged, indent=2), encoding="utf-8")
    report.update({"counts": counts, "files": files, "duration_ms": duration_ms, "hs_requests": stats.hs_requests,
                   "index_sizes": {k: v.count for k, v in indexes.items()}, "run_id": run_id, "out_dir": str(out_dir)})
    (out_dir / "load_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


async def ingest_into_fake(spec: SourceSpec, main: dict[str, dict], derived: dict[str, dict[str, dict]], properties_to_create: dict[str, list[dict]],
                           client: HubSpotClient | None = None) -> dict:
    client = client or get_client()
    if not client.is_fake:
        raise RuntimeError("ingest_fake refused: the HubSpot client points at the real HubSpot API")
    out: dict = {}
    stats = RequestStats()
    for object_type, recs in list(derived.items()) + [(spec.object, main)]:
        payload = {"object": object_type, "properties_def": properties_to_create.get(object_type, []),
                   "records": [{k: v for k, v in r.items() if not k.startswith("_")} for r in recs.values()]}
        out[object_type] = await client.post("/_fake/ingest", payload, stats=stats)
    return out
