from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routes import hubspot

BACKEND_DIR = Path(__file__).resolve().parents[1]

app = FastAPI(
    title=settings.project_name,
    # Keep the OpenAPI spec under /api so it rides the same /api proxy
    # (Vite in dev, nginx in prod) as every other backend route.
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# No CORS middleware on purpose: the frontend reaches this API same-origin
# in every mode (Vite /api proxy in dev, nginx /api proxy in compose, this
# process serving the bundle on Cloud Run), and a wildcard policy would let
# any page in a local browser read CRM data from the published port.

app.include_router(hubspot.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "project": settings.project_name}


def mount_spa(app: FastAPI, static_dir: Path) -> bool:
    """Serve the built React bundle from this process (single-container mode).

    Registered LAST so every /api route keeps precedence. Returns False and
    changes nothing when static_dir has no index.html — dev (Vite proxy) and
    compose (nginx) run exactly as before.
    """
    static_dir = static_dir.resolve()
    index = static_dir / "index.html"
    if not index.is_file():
        return False

    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (static_dir / full_path).resolve()
        if full_path and candidate.is_file() and static_dir in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)

    return True


mount_spa(app, BACKEND_DIR / settings.static_dir)
