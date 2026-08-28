from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routes import hubspot

app = FastAPI(
    title=settings.project_name,
    # Keep the OpenAPI spec under /api so it rides the same /api proxy
    # (Vite in dev, nginx in prod) as every other backend route.
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

app.include_router(hubspot.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "project": settings.project_name}
