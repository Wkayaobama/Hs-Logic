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

    # HubSpot API base (overridable to point the backend at the fake HubSpot
    # used by tests and by the sampling probe when no real token is at hand).
    hubspot_base_url: str = "https://api.hubapi.com"
    hubspot_max_retries: int = 3
    # Search endpoints are limited to 5 req/s per account; stay under it.
    search_rps: float = 4.0
    export_page_limit: int = 100
    # Empty -> app/data/entities.yaml
    entities_path: str = ""


settings = Settings()
