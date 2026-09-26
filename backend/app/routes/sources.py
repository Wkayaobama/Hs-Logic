"""Ad-hoc source routes: profile a file, match its columns to CRM properties, load it into the probe surface."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response

from app.config import settings, get_ruler_dir, get_sampling_dir
from app.hubspot.client import RequestStats, get_client
from app.sources import load as loader
from app.sources.match import build_spec, match_columns, schema_proposal
from app.sources.models import LoadRequest, MatchRequest, ProfileRequest, SourceSpec
from app.sources.profile import profile_columns, read_rows, sniff

router = APIRouter(prefix="/api/sources", tags=["sources"])
OBJECTS = {"contacts", "companies", "deals", "tickets"}


def allowed_roots() -> list[Path]:
    roots = [get_ruler_dir(), get_sampling_dir(), Path(__file__).resolve().parents[2]]
    for extra in (settings.sources_extra_dirs or "").split(":"):
        if extra.strip():
            roots.append(Path(extra.strip()))
    return [r.resolve() for r in roots]


def resolve_path(raw: str, *, must_exist: bool = True) -> Path:
    p = Path(raw)
    candidates = [p] if p.is_absolute() else [get_ruler_dir() / p, get_sampling_dir() / p]
    for c in candidates:
        rc = c.resolve()
        if any(str(rc).startswith(str(root) + "/") or rc == root for root in allowed_roots()):
            if not must_exist or rc.exists():
                return rc
    raise HTTPException(status_code=400, detail=f"Path '{raw}' is outside the allowed roots or does not exist ({', '.join(str(r) for r in allowed_roots())}).")


def entity_dir(entity: str) -> Path:
    return get_ruler_dir() / "entities" / entity.upper()


@router.post("/profile")
async def profile_source(req: ProfileRequest):
    path = resolve_path(req.file)
    delimiter, header_row, header = sniff(path, encoding=req.encoding, delimiter=req.delimiter, header_row=req.header_row)
    header, rows = read_rows(path, encoding=req.encoding, delimiter=delimiter, header_row=header_row, limit=req.sample_rows)
    return {"file": str(path), "delimiter": delimiter, "header_row": header_row, "rows_sampled": len(rows), "columns": profile_columns(header, rows)}


@router.post("/match")
async def match_source(req: MatchRequest, response: Response):
    if req.object not in OBJECTS:
        raise HTTPException(status_code=400, detail=f"object must be one of {sorted(OBJECTS)}")
    path = resolve_path(req.file)
    delimiter, header_row, _ = sniff(path, encoding=req.encoding, delimiter=req.delimiter, header_row=req.header_row)
    header, rows = read_rows(path, encoding=req.encoding, delimiter=delimiter, header_row=header_row, limit=req.sample_rows)
    profiles = profile_columns(header, rows)
    stats = RequestStats()
    target = await get_client().get_properties(req.object, stats=stats)
    name = req.name or path.stem
    mappings, derived, notes, keys = match_columns(entity=req.entity, object_type=req.object, profiles=profiles, target_props=target,
                                                   source_name=name, key_columns=req.key_columns, source_system=req.source_system)
    try:
        rel_file = str(path.relative_to(get_ruler_dir()))
    except ValueError:
        rel_file = str(path)
    spec = build_spec(name=name, entity=req.entity, object_type=req.object, file=rel_file, encoding=req.encoding, delimiter=delimiter,
                      header_row=header_row, mappings=mappings, derived=derived, keys=keys, notes=notes, source_system=req.source_system)
    proposal = schema_proposal(req.entity, req.object, mappings, derived, name)
    saved: dict[str, str] = {}
    if req.save:
        d = entity_dir(req.entity) / "sources"
        d.mkdir(parents=True, exist_ok=True)
        spec_path = d / f"{name}.source.json"
        spec_path.write_text(spec.model_dump_json(indent=2, exclude={"mappings": {"__all__": {"profile"}}}), encoding="utf-8")
        saved["spec"] = str(spec_path)
        merged_path = entity_dir(req.entity) / "schema.proposed.json"
        merged: dict = json.loads(merged_path.read_text(encoding="utf-8")) if merged_path.exists() else {"entity": req.entity.upper(), "properties": {}}
        for obj, props in proposal.items():
            existing = {p["name"]: p for p in merged["properties"].get(obj, [])}
            for p in props:
                existing.setdefault(p["name"], p)
            merged["properties"][obj] = sorted(existing.values(), key=lambda p: p["name"])
        merged_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        saved["schema_proposal"] = str(merged_path)
    for k, v in stats.headers().items():
        response.headers[k] = v
    summary = {
        "columns": len(mappings),
        "auto": sum(1 for m in mappings if m.decision == "auto"), "review": sum(1 for m in mappings if m.decision == "review"),
        "unmapped": sum(1 for m in mappings if m.decision == "unmapped"), "ignored": sum(1 for m in mappings if m.decision == "ignored"),
        "derived_objects": [d.object for d in derived], "keys": keys, "target_properties": len(target), "proposed_properties": {o: len(p) for o, p in proposal.items()},
    }
    return {"spec": spec, "schema_proposal": proposal, "summary": summary, "saved": saved, "stats": stats.as_dict()}


@router.get("/specs")
async def list_specs(entity: str | None = None):
    root = get_ruler_dir() / "entities"
    out = []
    for spec_path in sorted(root.glob("*/sources/*.source.json")):
        try:
            spec = SourceSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            out.append({"path": str(spec_path), "error": str(exc)})
            continue
        if entity and spec.entity.upper() != entity.upper():
            continue
        out.append({"path": str(spec_path.relative_to(get_ruler_dir())), "name": spec.name, "entity": spec.entity, "object": spec.object,
                    "file": spec.file, "status": spec.status, "columns": len(spec.mappings), "derived": [d.object for d in spec.derived],
                    "review": sum(1 for m in spec.mappings if m.decision == "review"), "unmapped": sum(1 for m in spec.mappings if m.decision == "unmapped")})
    return {"ruler_dir": str(get_ruler_dir()), "specs": out}


@router.post("/load")
async def load_source(req: LoadRequest, response: Response):
    if req.spec_inline is not None:
        spec = req.spec_inline
    elif req.spec:
        spec = SourceSpec.model_validate_json(resolve_path(req.spec).read_text(encoding="utf-8"))
    else:
        raise HTTPException(status_code=400, detail="spec (path) or spec_inline is required")
    if req.out_dir:
        out_dir = resolve_path(req.out_dir, must_exist=False)
    elif req.run_id:
        out_dir = get_sampling_dir() / "extract" / spec.entity.upper() / req.run_id
    else:
        out_dir = entity_dir(spec.entity) / "sources" / "out" / spec.name
    report = await loader.load_source(spec, base_dir=get_ruler_dir(), out_dir=out_dir, run_id=req.run_id, match_records=req.match_records,
                                      ingest_fake=req.ingest_fake, limit=req.limit)
    response.headers["X-Logic-Request-Id"] = RequestStats().request_id
    return report
