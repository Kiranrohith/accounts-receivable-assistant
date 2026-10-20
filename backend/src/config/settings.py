from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str
    our_company_name: str | None = None  # the issuer of our invoices: never the customer
    our_gstin: str | None = None
    seed_admin_email: str | None = None
    seed_admin_password: str | None = None
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_from: str = "accounts@genworx.local"  # the From address of everything we send
    raw_files_dir: str = "/data/raw"
    # Job worker. In dev the API process runs one itself; in production set
    # RUN_EMBEDDED_WORKER=false and run `python -m jobs.runner` as separate containers.
    run_embedded_worker: bool = True
    worker_concurrency: int = 2
    gmail_refresh_token: str | None = None
    groq_key_1: str | None = None
    groq_key_2: str | None = None
    gemini_key: str | None = None
    primary_llm_model: str = "openai/gpt-oss-120b"
    fallback_llm_model: str = "gemini-3.5-flash-lite"


@lru_cache
def get_settings() -> Settings:
    return Settings()
