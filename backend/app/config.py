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
    scoring_cache_ttl_seconds: int = 900
    # Page-through guard for full-portal scans (health, scoring, probe).
    # Portal 9201667 holds ~25.7k contacts (probed 2026-08-31), so the old
    # 10k constant silently truncated every scan.
    scan_cap: int = 30000


settings = Settings()
