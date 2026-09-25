"""Test fixtures: the backend app wired to the deterministic fake HubSpot through ASGI transports."""

import os

os.environ.setdefault("HUBSPOT_TOKEN", "test-token")
os.environ.setdefault("HUBSPOT_PORTAL_ID", "9201667")

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.hubspot.client import HubSpotClient, set_client  # noqa: E402
from tools.fake_hubspot import FakeHubSpot, create_app  # noqa: E402


@pytest.fixture(scope="session")
def fake() -> FakeHubSpot:
    # Deals are always the full 1,960 (reference numbers); the rest is trimmed for speed.
    return FakeHubSpot(contacts=1000, companies=400, tickets=60, notes=120)


@pytest.fixture(scope="session")
def fake_app(fake):
    return create_app(fake)


@pytest.fixture
def hs_client(fake, fake_app):
    fake.inject_429_every = 0
    fake.search_cap = 10_000
    client = HubSpotClient(
        "test-token", "http://fake-hubspot", search_rps=0, max_retries=3,
        transport=httpx.ASGITransport(app=fake_app),
    )
    set_client(client)
    yield client
    set_client(None)


@pytest.fixture
async def api(hs_client):
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://logic") as c:
        yield c
