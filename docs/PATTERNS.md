# Pattern catalog — the Pythonic concepts behind each deployed pattern

Companion to [RUNBOOK.md](RUNBOOK.md). Each entry names a pattern that ships in
this service, the Python concept it is built on, and where it lives. Code is
referenced by function name rather than line number so this document survives
edits.

> Change policy reminder: the scan/route logic in
> `backend/app/routes/hubspot.py` is intentionally byte-for-byte from the
> deployed portal-explorer app (only the cache wrappers and the `/portal`
> count source were changed, each in its own commit). Understand these
> patterns before touching that file — and prefer not touching it.

---

## 1. Single-flight TTL cache

**Where:** `backend/app/cache.py` (`TTLCache.get_or_compute`), used by
`get_crm_health` / `get_contact_health` in `backend/app/routes/hubspot.py`.

**Pythonic concepts**

- **Coroutines as first-class values** — the compute step is passed as
  `Callable[[], Awaitable[dict]]`. The cache never knows what it caches; it
  awaits whatever coroutine factory it is handed. This is the async version of
  "pass behavior, not data".
- **Per-key `asyncio.Lock` via `collections.defaultdict(asyncio.Lock)`** — the
  canonical grouping idiom repurposed for concurrency: the first request for a
  key materializes its lock, every later request reuses it. Two different keys
  never contend.
- **Double-checked locking, async flavor** — a lock-free fast path reads the
  entry; only a miss takes the lock and re-checks before computing. Waiters
  that queued behind a running scan compare `entry["computed_at"] >= arrived`
  and reuse the just-finished result — even concurrent *refreshes* collapse
  into one scan. That comparison is the whole single-flight mechanism: two
  timestamps and an inequality.
- **`time.monotonic()` for intervals, wall-clock only for display** — TTL math
  uses the monotonic clock (immune to NTP jumps); the ISO timestamp shown to
  users is captured separately with `datetime.now(timezone.utc)`.
- **Non-mutating decoration with `{**data, "cache": {...}}`** — the cached
  payload is never mutated; each response is a fresh dict spread with one
  additive key. No consumer can corrupt the cache by editing its response.
- **Exceptions never cached** — a failed `compute()` propagates out of the
  `async with` block before the entry assignment runs. Failure handling costs
  zero code because the control flow already does the right thing (EAFP).

## 2. Thin decorated routes over extracted compute functions

**Where:** `get_crm_health(refresh: bool = False)` and
`get_contact_health(refresh: bool = False)` wrap `_compute_crm_health()` /
`_compute_contact_health()`.

**Pythonic concepts** — decorators as *registration* (`@router.get` attaches
transport; the function body stays pure), and separation of transport from
computation: the `_compute_*` functions are plain coroutines you can call from
a test, a script, or a future CLI without FastAPI in the room. The leading
underscore is the module-privacy convention doing real work: it marks what is
implementation and what is API.

## 3. Uniform transport core with exception translation

**Where:** `hs_headers`, `hs_get`, `hs_post` at the top of
`backend/app/routes/hubspot.py`.

**Pythonic concepts**

- **`async with httpx.AsyncClient(...)` context managers** — connection
  lifetime is scoped by indentation; no leaked sockets on any exit path.
- **Exception translation at the boundary** — HubSpot's 401/403/other are
  mapped once into `HTTPException` with operator-readable detail ("token is
  invalid or expired", "token lacks required scopes"). Every route above this
  layer is written as if the happy path is the only path — errors ride the
  exception channel instead of being threaded through return values.
- **EAFP (easier to ask forgiveness than permission)** — the
  `final_customer` deal search is wrapped in `try/except HTTPException: break`
  so a portal without that property degrades to an empty set instead of
  requiring a lookahead "does this property exist?" call. Same idiom guards
  the v4 association batch reads.

## 4. Cursor pagination with a hard cap

**Where:** the company/contact scan loops in `_compute_crm_health` /
`_compute_contact_health`, and the fc-deals search loop.

**Pythonic concepts** — a `while len(acc) < CAP` sentinel loop advancing on
`data.get("paging", {}).get("next", {}).get("after")`; chained `.get()` with
dict defaults turns a deeply optional JSON shape into one expression with no
conditionals. The `CAP = 10000` guard plus the `capped` flag in the payload is
the "make partial results explicit, never silent" rule — truncation is data,
not a surprise.

## 5. Set membership for the SQL anti-join

**Where:** `fc_names` set comprehension and the orphan filter in
`_compute_crm_health`.

**Pythonic concepts** — the SQL `NOT IN (SELECT ...)` becomes a set
comprehension (`{_normalize(d["final_customer"]) for d in fc_deals}`) plus
O(1) membership tests inside a plain loop. Three CTEs collapse into three
Python collections (`assoc` → inline association counts, `edge_companies` →
`edge_company_ids` set, `fc_names` → set) and the final `WHERE` clause is
three `if ... continue` lines. Normalization (`_normalize`: lowercase +
whitespace collapse via `re.sub`) is centralized so both sides of every
comparison agree.

## 6. Grouping with `defaultdict(list)` + `frozenset` identity dedup

**Where:** duplicate-cluster detection in both compute functions
(`domain_groups` / `name_groups`, `email_groups` / `name_groups`, and
`seen_cluster_ids: set[frozenset]`).

**Pythonic concepts** — `defaultdict(list)` is *the* grouping idiom
(`groups[key].append(item)`, no key-exists dance). The subtle one is
`frozenset` for cluster identity: a cluster found by domain and again by name
has the same member ids; freezing that id set makes it hashable, so
"have we already emitted this cluster?" is one set lookup regardless of which
key found it first.

## 7. Declarative configuration as a class

**Where:** `backend/app/config.py` (`Settings(BaseSettings)`, module-level
`settings` singleton).

**Pythonic concepts** — class-as-schema: typed attributes with defaults *are*
the config contract, and pydantic-settings maps `HUBSPOT_TOKEN` →
`hubspot_token` by convention. The `env_file=(".env", "../.env")` tuple
encodes "works from repo root and from backend/" as data. One module-level
instance imported everywhere (`from app.config import settings`) is the
sanctioned singleton form — module import machinery is the singleton.

## 8. Chunked batch writes with rate headroom

**Where:** `create_suppression_list` (chunks of 100, `await asyncio.sleep(0.12)`
between chunks) and the v4 association batch reads
(`for i in range(0, len(ids), 100): chunk = ids[i:i+100]`).

**Pythonic concepts** — the `range(0, n, step)` + slice idiom for batching
(slices past the end are safe, so no bounds arithmetic), and cooperative
politeness via `asyncio.sleep` — yielding the event loop *is* the rate
limiter. Deliberately sequential: the scans and writes stay under HubSpot's
burst limit because nothing is `gather`ed. Do not "optimize" this.

## 9. Sentinels for absent data

**Where:** the `count()` helper inside `get_portal_info` (returns `-1` when a
total is unavailable; the frontend renders it as an em dash) and the `capped`
boolean on both health payloads.

**Pythonic concepts** — explicit sentinel over silent default: `-1` is
distinguishable from a real zero, and the `isinstance(total, int)` guard
refuses to forward whatever shape an API drift might produce. The earlier bug
this replaced — `data.get("total", 0)` on an envelope that never carries
`total` — is the cautionary tale: a default that shadows absence *is* the bug.

## 10. Application as an importable module

**Where:** `backend/app/main.py` (`app = FastAPI(...)` at module scope, run by
`uvicorn app.main:app`).

**Pythonic concepts** — the ASGI entry point is just an attribute lookup on an
imported module; there is no `if __name__ == "__main__"` block and no
side-effectful startup code beyond object construction. Importing the app is
safe (tests do it via `TestClient`), which is what made the token-free
verification in CI-style checks possible. The deliberate *absence* of CORS
middleware is part of the pattern: the frontend is same-origin in every mode
(Vite proxy in dev, nginx proxy in prod), so the API surface adds nothing it
does not need.

---

## Frontend mirrors (for orientation, not Pythonic)

The TypeScript side deliberately mirrors several of these: `useApi` is the
cache-consumer with a race-guard sequence ref (the monotonic-comparison trick
in another language), `RecordList` is the cursor-pagination loop as a
component, and `escapeCsvField` is exception-translation's cousin — one
boundary function through which all untrusted text passes (OWASP formula
neutralization + RFC 4180 quoting).
