"""Shared HubSpot HTTP client.

One connection pool for the whole process, backoff on 429/5xx that honours
``Retry-After`` (bounded, never an endless loop), list pagination (no cap),
search pagination (200 per page, rate limited, 10k cap detected and flagged),
v4 association batch reads that keep ``typeId``/``label``, and per-request
statistics that the routes expose as ``X-Logic-*`` headers so the sampling
probe can measure efficiency.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import HTTPException

from app.config import settings

SEARCH_HARD_CAP = 10_000
SEARCH_PAGE_MAX = 200
LIST_PAGE_MAX = 100
BATCH_MAX = 100
MAX_RETRY_AFTER_SECONDS = 30.0


@dataclass
class RequestStats:
    """Counters for one API request handled by this backend."""

    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    hs_requests: int = 0
    hs_429: int = 0
    retries: int = 0
    capped: bool = False
    started: float = field(default_factory=time.perf_counter)

    @property
    def duration_ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)

    def as_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "hs_requests": self.hs_requests,
            "hs_429": self.hs_429,
            "retries": self.retries,
            "capped": self.capped,
            "duration_ms": self.duration_ms,
        }

    def headers(self) -> dict[str, str]:
        return {
            "X-Logic-Request-Id": self.request_id,
            "X-Logic-Duration-Ms": str(self.duration_ms),
            "X-Logic-HS-Requests": str(self.hs_requests),
            "X-Logic-HS-429": str(self.hs_429),
            "X-Logic-HS-Retries": str(self.retries),
        }


class RateLimiter:
    """Minimum spacing between calls; enough for HubSpot's 5 req/s search limit."""

    def __init__(self, rps: float) -> None:
        self._interval = 1.0 / rps if rps and rps > 0 else 0.0
        self._next_at = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if not self._interval:
            return
        async with self._lock:
            now = time.monotonic()
            if now < self._next_at:
                await asyncio.sleep(self._next_at - now)
                now = time.monotonic()
            self._next_at = now + self._interval


def _message(resp: httpx.Response) -> str:
    if not resp.content:
        return resp.reason_phrase or str(resp.status_code)
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:300]
    if isinstance(data, dict):
        return str(data.get("message") or data)[:300]
    return str(data)[:300]


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return max(0.0, min(value, MAX_RETRY_AFTER_SECONDS))


def _next_after(data: dict) -> str | None:
    after = ((data.get("paging") or {}).get("next") or {}).get("after")
    return str(after) if after not in (None, "") else None


def chunks(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class HubSpotClient:
    def __init__(
        self,
        token: str,
        base_url: str = "https://api.hubapi.com",
        *,
        search_rps: float = 4.0,
        max_retries: int = 3,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._max_retries = max(0, max_retries)
        self._timeout = timeout
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._search_limiter = RateLimiter(search_rps)

    # ── plumbing ──────────────────────────────────────────────────────────

    def _ensure(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url, timeout=self._timeout, transport=self._transport
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        stats: RequestStats | None = None,
    ) -> Any:
        if not self._token:
            raise HTTPException(status_code=500, detail="HubSpot token not configured.")
        stats = stats or RequestStats()
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        attempt = 0
        while True:
            client = self._ensure()
            try:
                resp = await client.request(method, path, params=params, json=json, headers=headers)
            except httpx.HTTPError as exc:  # network / timeout
                raise HTTPException(
                    status_code=502, detail=f"HubSpot unreachable: {exc.__class__.__name__}"
                ) from exc
            stats.hs_requests += 1

            if resp.status_code == 429 or resp.status_code >= 500:
                if resp.status_code == 429:
                    stats.hs_429 += 1
                if attempt >= self._max_retries:
                    raise HTTPException(
                        status_code=resp.status_code,
                        detail=f"HubSpot error after {attempt + 1} attempts: {_message(resp)}",
                    )
                delay = _retry_after(resp)
                if delay is None:
                    delay = float(min(2**attempt, 10))
                attempt += 1
                stats.retries += 1
                await asyncio.sleep(delay)
                continue

            if resp.status_code == 401:
                raise HTTPException(status_code=401, detail="HubSpot token is invalid or expired.")
            if resp.status_code == 403:
                raise HTTPException(status_code=403, detail="HubSpot token lacks required scopes.")
            if not resp.is_success:
                raise HTTPException(
                    status_code=resp.status_code, detail=f"HubSpot error: {_message(resp)}"
                )
            if not resp.content:
                return {}
            return resp.json()

    async def get(self, path: str, params: dict | None = None, *, stats: RequestStats | None = None) -> Any:
        return await self.request("GET", path, params=params, stats=stats)

    async def post(self, path: str, json: Any, *, stats: RequestStats | None = None) -> Any:
        return await self.request("POST", path, json=json, stats=stats)

    # ── CRM helpers ───────────────────────────────────────────────────────

    async def list_page(
        self,
        object_type: str,
        *,
        properties: list[str],
        after: str | None,
        limit: int,
        stats: RequestStats,
    ) -> tuple[list[dict], str | None]:
        """One page of the plain list endpoint (no 10k cap, id order)."""
        params: dict[str, Any] = {
            "limit": max(1, min(limit, LIST_PAGE_MAX)),
            "properties": ",".join(properties),
        }
        if after:
            params["after"] = after
        data = await self.get(f"/crm/v3/objects/{object_type}", params, stats=stats)
        return data.get("results", []), _next_after(data)

    async def search_page(
        self,
        object_type: str,
        *,
        filter_groups: list[dict] | None,
        properties: list[str],
        after: str | None,
        limit: int,
        sorts: list | None = None,
        stats: RequestStats,
    ) -> tuple[list[dict], str | None, int | None]:
        """One page of the search endpoint; flags ``stats.capped`` at the 10k ceiling."""
        body: dict[str, Any] = {"properties": properties, "limit": max(1, min(limit, SEARCH_PAGE_MAX))}
        if filter_groups:
            body["filterGroups"] = filter_groups
        if after:
            body["after"] = after
        if sorts:
            body["sorts"] = sorts
        await self._search_limiter.wait()
        data = await self.post(f"/crm/v3/objects/{object_type}/search", body, stats=stats)
        results = data.get("results", [])
        total = data.get("total")
        next_after = _next_after(data)
        try:
            offset = int(after) if after else 0
        except ValueError:
            offset = 0
        if next_after is None and isinstance(total, int) and offset + len(results) < total:
            stats.capped = True
        if next_after is not None:
            try:
                if int(next_after) >= SEARCH_HARD_CAP:
                    stats.capped = True
                    next_after = None
            except ValueError:
                pass
        return results, next_after, total if isinstance(total, int) else None

    async def batch_read_associations(
        self,
        from_type: str,
        to_type: str,
        ids: list[str],
        *,
        stats: RequestStats,
    ) -> dict[str, list[dict]]:
        """v4 batch read; returns {from_id: [{id, typeId, label, category}, ...]}."""
        out: dict[str, list[dict]] = {str(i): [] for i in ids}
        for chunk in chunks([str(i) for i in ids], BATCH_MAX):
            data = await self.post(
                f"/crm/v4/associations/{from_type}/{to_type}/batch/read",
                {"inputs": [{"id": i} for i in chunk]},
                stats=stats,
            )
            for row in data.get("results", []):
                from_id = str((row.get("from") or {}).get("id"))
                bucket = out.setdefault(from_id, [])
                for target in row.get("to", []):
                    to_id = str(target.get("toObjectId"))
                    types = target.get("associationTypes") or []
                    if not types:
                        bucket.append({"id": to_id, "typeId": None, "label": None, "category": None})
                    for at in types:
                        bucket.append(
                            {
                                "id": to_id,
                                "typeId": at.get("typeId"),
                                "label": at.get("label"),
                                "category": at.get("category"),
                            }
                        )
        return out

    async def get_properties(self, object_type: str, *, stats: RequestStats) -> list[dict]:
        data = await self.get(f"/crm/v3/properties/{object_type}", stats=stats)
        return data.get("results", [])


# ── process-wide singleton ────────────────────────────────────────────────

_client: HubSpotClient | None = None


def get_client() -> HubSpotClient:
    global _client
    if _client is None:
        _client = HubSpotClient(
            settings.hubspot_token,
            settings.hubspot_base_url,
            search_rps=settings.search_rps,
            max_retries=settings.hubspot_max_retries,
        )
    return _client


def set_client(client: HubSpotClient | None) -> None:
    """Replace the singleton (tests inject a client with a mock transport)."""
    global _client
    _client = client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
