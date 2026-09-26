"""CLI twin of the /api/sources routes (uses HUBSPOT_BASE_URL / HUBSPOT_TOKEN from the environment).

  python -m app.sources.cli profile ../HubSpot-Ruler/entities/WECAN/sources/wecan_contacts.csv
  python -m app.sources.cli match WECAN contacts entities/WECAN/sources/wecan_contacts.csv --save
  python -m app.sources.cli load entities/WECAN/sources/wecan_contacts.source.json --run-id file1 --ingest-fake
"""

from __future__ import annotations

import argparse
import asyncio
import json

from fastapi import Response

from app.routes import sources as routes
from app.sources.models import LoadRequest, MatchRequest, ProfileRequest


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("profile"); p.add_argument("file"); p.add_argument("--header-row", type=int)
    m = sub.add_parser("match"); m.add_argument("entity"); m.add_argument("object"); m.add_argument("file"); m.add_argument("--name"); m.add_argument("--save", action="store_true"); m.add_argument("--source-system", default="")
    l = sub.add_parser("load"); l.add_argument("spec"); l.add_argument("--run-id"); l.add_argument("--out-dir"); l.add_argument("--ingest-fake", action="store_true"); l.add_argument("--no-match", action="store_true"); l.add_argument("--limit", type=int)
    args = parser.parse_args()
    resp = Response()
    if args.cmd == "profile":
        out = asyncio.run(routes.profile_source(ProfileRequest(file=args.file, header_row=args.header_row)))
    elif args.cmd == "match":
        out = asyncio.run(routes.match_source(MatchRequest(entity=args.entity, object=args.object, file=args.file, name=args.name, save=args.save, source_system=args.source_system), resp))
    else:
        out = asyncio.run(routes.load_source(LoadRequest(spec=args.spec, run_id=args.run_id, out_dir=args.out_dir, ingest_fake=args.ingest_fake, match_records=not args.no_match, limit=args.limit), resp))
    print(json.dumps(out, indent=2, ensure_ascii=False, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o)))


if __name__ == "__main__":
    main()
