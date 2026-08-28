from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient

from app import analytics, error_tracker
from app.config import settings
from app.routes import hubspot


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = AsyncIOMotorClient(settings.mongo_url)
    app.state.mongo = client
    app.state.db = client[settings.mongo_db]
    try:
        yield
    finally:
        client.close()


app = FastAPI(
    title=settings.project_name,
    lifespan=lifespan,
    # Expose the OpenAPI spec under the /api prefix so it's reachable
    # through Vite's /api proxy from the preview URL. The orchestrator's
    # Routes panel reads this to auto-discover the app's endpoints.
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Builder-internal: tracks runtime errors so the App Builder can surface them
# as a "Send to agent" popup. Safe to keep in production builds; it has a
# fixed in-memory ring buffer and only adds /api/__app_errors routes.
error_tracker.install(app)
app.include_router(hubspot.router)

# Builder-internal: lightweight request analytics powering the Backend /
# Analytics tab. Records every request to a capped Mongo collection;
# /api/__analytics/summary serves aggregates the orchestrator proxies.
# Internal routes (/api/__*, /docs, etc.) are skipped automatically.
analytics.install(app)


@app.get("/api/health")
async def health():
    return {"status": "ok", "project": settings.project_name}


@app.get("/api/hello")
async def hello():
    db = app.state.db
    await db.greetings.update_one(
        {"_id": "default"},
        {
            "$inc": {"count": 1},
            "$setOnInsert": {"message": f"hello from {settings.project_name}"},
        },
        upsert=True,
    )
    doc = await db.greetings.find_one({"_id": "default"})
    return {"message": doc["message"], "count": doc["count"]}
