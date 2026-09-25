import httpx
import pytest
from fastapi import HTTPException

from app.hubspot.client import HubSpotClient, RequestStats


async def test_list_pagination_walks_every_deal(hs_client):
    stats = RequestStats()
    after, seen, pages = None, 0, 0
    while True:
        results, after = await hs_client.list_page("deals", properties=["dealname"], after=after, limit=100, stats=stats)
        seen += len(results)
        pages += 1
        if not after:
            break
    assert seen == 1960
    assert pages == 20
    assert stats.hs_requests == 20
    assert not stats.capped


async def test_search_flags_the_cap(hs_client, fake):
    fake.search_cap = 150
    stats = RequestStats()
    results, after, total = await hs_client.search_page(
        "deals", filter_groups=None, properties=["dealname"], after=None, limit=100, stats=stats
    )
    assert len(results) == 100 and after == "100" and total == 1960 and not stats.capped
    results, after, total = await hs_client.search_page(
        "deals", filter_groups=None, properties=["dealname"], after="100", limit=100, stats=stats
    )
    assert len(results) == 100 and after is None
    assert stats.capped, "search stopped before total -> capped must be flagged"


async def test_backoff_recovers_from_429(hs_client, fake):
    fake.requests = 1  # the next request is the 2nd -> throttled once, then served
    fake.inject_429_every = 2
    fake.retry_after = "0"
    stats = RequestStats()
    results, _ = await hs_client.list_page("companies", properties=["name"], after=None, limit=10, stats=stats)
    assert len(results) == 10
    assert stats.hs_429 == 1 and stats.retries == 1
    assert stats.hs_requests == 2


async def test_gives_up_after_max_retries(hs_client, fake):
    fake.inject_429_every = 1  # every request throttled
    stats = RequestStats()
    with pytest.raises(HTTPException) as exc:
        await hs_client.list_page("companies", properties=["name"], after=None, limit=10, stats=stats)
    assert exc.value.status_code == 429
    assert stats.hs_requests == 4  # 1 + 3 retries, never an endless loop


async def test_association_labels_survive_batch_read(hs_client, fake):
    deals = fake.data.records["deals"][:25]
    stats = RequestStats()
    out = await hs_client.batch_read_associations("deals", "companies", [d["id"] for d in deals], stats=stats)
    assert stats.hs_requests == 1
    labelled = [a for links in out.values() for a in links]
    assert {a["typeId"] for a in labelled} >= {341, 5}
    assert any(a["label"] == "Primary" for a in labelled)
    assert all(a["category"] in ("HUBSPOT_DEFINED", "USER_DEFINED") for a in labelled)


async def test_batch_read_chunks_by_100(hs_client, fake):
    ids = [d["id"] for d in fake.data.records["deals"][:250]]
    stats = RequestStats()
    out = await hs_client.batch_read_associations("deals", "contacts", ids, stats=stats)
    assert stats.hs_requests == 3
    assert set(out) >= set(ids)


@pytest.mark.parametrize("token,status", [("expired", 401), ("noscope", 403)])
async def test_auth_errors_are_mapped(fake_app, token, status):
    client = HubSpotClient(token, "http://fake-hubspot", search_rps=0, transport=httpx.ASGITransport(app=fake_app))
    with pytest.raises(HTTPException) as exc:
        await client.get("/account-info/v3/details")
    assert exc.value.status_code == status
    await client.aclose()


async def test_missing_token_is_a_500_not_a_hubspot_call(fake_app):
    client = HubSpotClient("", "http://fake-hubspot", transport=httpx.ASGITransport(app=fake_app))
    with pytest.raises(HTTPException) as exc:
        await client.get("/account-info/v3/details")
    assert exc.value.status_code == 500
