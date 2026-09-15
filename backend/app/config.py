from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://kfc:kfc@localhost:5433/kfc"
    cors_origins: str = "*"

    # Optional LLM for summaries / better embeddings. Empty = local fallbacks,
    # so the whole stack runs standalone with zero API keys.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    embedding_dim: int = 256

    class Config:
        env_file = ".env"
        env_prefix = "KFC_"


@lru_cache
def get_settings() -> Settings:
    return Settings()
