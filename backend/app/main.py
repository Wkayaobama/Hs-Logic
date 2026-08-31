from fastapi import FastAPI

from app.config import settings
from app.routes import hubspot, scoring

app = FastAPI(
    title=settings.project_name,
    # Keep the OpenAPI spec under /api so it rides the same /api proxy
    # (Vite in dev, nginx in prod) as every other backend route.
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# No CORS middleware on purpose: the frontend reaches this API same-origin
# in every mode (Vite /api proxy in dev, nginx /api proxy in prod), and a
# wildcard policy would let any page in a local browser read CRM data from
# the published 127.0.0.1:8000 port.

app.include_router(hubspot.router)
app.include_router(scoring.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "project": settings.project_name}
