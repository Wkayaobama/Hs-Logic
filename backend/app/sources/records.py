"""Row -> existing CRM record matching (companies by name/domain, contacts by email/name, deals by name, any object by source key)."""

from __future__ import annotations

import re

from app.hubspot.client import HubSpotClient, RequestStats

from .match import jaccard, norm, tokens

LEGAL = re.compile(r"\b(sa|sas|ag|gmbh|inc|ltd|llc|plc|srl|bv|co|corp|corporation|company|limited|holding|group)\b")


def norm_name(text: str) -> str:
    t = re.sub(r"[^\w\s]", " ", (text or "").lower(), flags=re.UNICODE)
    t = LEGAL.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def norm_domain(text: str) -> str:
    t = (text or "").lower().strip()
    t = re.sub(r"^https?://", "", t)
    t = re.sub(r"^www\.", "", t)
    return t.split("/")[0]


INDEX_PROPERTIES = {
    "companies": ["name", "domain"],
    "contacts": ["email", "firstname", "lastname"],
    "deals": ["dealname"],
    "tickets": ["subject"],
}


class RecordIndex:
    def __init__(self, object_type: str, entity: str) -> None:
        self.object_type = object_type
        self.source_key_property = f"{norm(entity)}_source_key"
        self.by: dict[str, dict[str, list[str]]] = {"name": {}, "domain": {}, "email": {}, "source_key": {}, "id": {}}
        self.names: dict[str, str] = {}
        self.count = 0

    def add(self, rec: dict) -> None:
        p = rec.get("properties") or {}
        rid = str(rec["id"])
        self.count += 1
        self.by["id"].setdefault(rid, []).append(rid)
        sk = p.get(self.source_key_property)
        if sk:
            self.by["source_key"].setdefault(str(sk), []).append(rid)
        if self.object_type == "companies":
            n = norm_name(p.get("name") or "")
            d = norm_domain(p.get("domain") or "")
            if n:
                self.by["name"].setdefault(n, []).append(rid); self.names[rid] = n
            if d:
                self.by["domain"].setdefault(d, []).append(rid)
        elif self.object_type == "contacts":
            e = (p.get("email") or "").lower().strip()
            n = norm_name(f"{p.get('firstname') or ''} {p.get('lastname') or ''}")
            if e:
                self.by["email"].setdefault(e, []).append(rid)
            if n:
                self.by["name"].setdefault(n, []).append(rid); self.names[rid] = n
        else:
            n = norm_name(p.get("dealname") or p.get("subject") or "")
            if n:
                self.by["name"].setdefault(n, []).append(rid); self.names[rid] = n

    def match(self, match_by: str, value: str, *, fuzzy: float = 0.85) -> tuple[str | None, float, str]:
        if not value:
            return None, 0.0, "empty value"
        key = {"name": norm_name, "domain": norm_domain, "email": lambda v: v.lower().strip()}.get(match_by, lambda v: str(v).strip())(value)
        hits = self.by.get(match_by, {}).get(key, [])
        if len(hits) == 1:
            return hits[0], 1.0, f"exact {match_by}"
        if len(hits) > 1:
            return hits[0], 0.9, f"exact {match_by}, {len(hits)} candidates (first kept)"
        if match_by == "name" and key:
            toks = tokens(key)
            best, best_score = None, 0.0
            block = key[:3]
            for rid, name in self.names.items():
                if not name.startswith(block):
                    continue
                s = jaccard(toks, tokens(name))
                if s > best_score:
                    best, best_score = rid, s
            if best is not None and best_score >= fuzzy:
                return best, best_score, f"fuzzy name {best_score}"
        return None, 0.0, "no match"


async def build_index(client: HubSpotClient, object_type: str, entity: str, *, max_records: int = 60000, stats: RequestStats | None = None) -> RecordIndex:
    stats = stats or RequestStats()
    index = RecordIndex(object_type, entity)
    props = INDEX_PROPERTIES.get(object_type, ["name"]) + [index.source_key_property]
    after = None
    while True:
        results, after = await client.list_page(object_type, properties=props, after=after, limit=100, stats=stats)
        for r in results:
            index.add(r)
        if not after or index.count >= max_records:
            break
    return index
