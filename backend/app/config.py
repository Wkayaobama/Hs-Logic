from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings; env vars win over the .env file.

    env_file lists both the CWD .env and the repo-root ../.env so uvicorn
    launched from backend/ still picks up the single root .env.
    """

    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    project_name: str = "hubspot-logic-server"
    hubspot_token: str = ""
    hubspot_portal_id: str = ""
    health_cache_ttl_seconds: int = 900
    # Directory holding the built React bundle (index.html + assets/). Relative
    # paths resolve against backend/. Absent in dev (Vite proxy) and in compose
    # (nginx serves the SPA); present in the single-container Cloud Run image.
    static_dir: str = "static"


settings = Settings()
