"""In-process TTL cache with single-flight semantics.

Used by the health endpoints, whose scans each cost 100+ sequential
HubSpot API calls. Per-process only: run a single uvicorn worker, or
each worker keeps (and recomputes) its own copy.
"""

import asyncio
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone


class TTLCache:
    def __init__(self) -> None:
        self._entries: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def get_or_compute(
        self,
        key: str,
        compute: Callable[[], Awaitable[dict]],
        ttl_seconds: int,
        refresh: bool = False,
    ) -> dict:
        arrived = time.monotonic()
        entry = self._entries.get(key)
        if entry and not refresh and arrived - entry["computed_at"] < ttl_seconds:
            return self._decorate(entry, ttl_seconds, cached=True)

        async with self._locks[key]:
            entry = self._entries.get(key)
            if entry:
                # An entry stamped after our arrival was computed while we
                # waited on the lock — reuse it even for refresh requests,
                # so concurrent refreshes trigger one scan, not several.
                just_computed = entry["computed_at"] >= arrived
                still_valid = time.monotonic() - entry["computed_at"] < ttl_seconds
                if just_computed or (not refresh and still_valid):
                    return self._decorate(entry, ttl_seconds, cached=True)

            data = await compute()
            entry = {
                "data": data,
                "computed_at": time.monotonic(),
                "computed_at_iso": datetime.now(timezone.utc).isoformat(),
            }
            self._entries[key] = entry
            return self._decorate(entry, ttl_seconds, cached=False)

    def _decorate(self, entry: dict, ttl_seconds: int, cached: bool) -> dict:
        age = max(0, int(time.monotonic() - entry["computed_at"]))
        return {
            **entry["data"],
            "cache": {
                "cached": cached,
                "computed_at": entry["computed_at_iso"],
                "age_seconds": age,
                "ttl_seconds": ttl_seconds,
            },
        }


health_cache = TTLCache()
scoring_cache = TTLCache()
