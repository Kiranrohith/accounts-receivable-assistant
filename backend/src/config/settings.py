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
    raw_files_dir: str = "/data/raw"
    gmail_refresh_token: str | None = None
    groq_key_1: str | None = None
    groq_key_2: str | None = None
    gemini_key: str | None = None
    primary_llm_model: str = "openai/gpt-oss-120b"
    fallback_llm_model: str = "gemini-3.5-flash-lite"


@lru_cache
def get_settings() -> Settings:
    return Settings()
