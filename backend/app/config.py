from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://kfc:kfc@localhost:5433/kfc"
    cors_origins: str = "*"

    # Optional LLM for summaries / better embeddings. Empty = local fallbacks,
    # so the whole stack runs standalone with zero API keys.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"

    embedding_dim: int = 256

    # Boot seeding (sample orders/customers/calls when tables are empty).
    # Dev default true; production overlay forces false so prod stays clean
    # and a wipe is never silently re-seeded on restart.
    seed_enabled: bool = True

    class Config:
        env_file = ".env"
        env_prefix = "KFC_"


@lru_cache
def get_settings() -> Settings:
    return Settings()
