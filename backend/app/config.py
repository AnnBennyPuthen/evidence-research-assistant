from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_mode: Literal["demo", "live"] = "demo"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    tavily_api_key: str | None = None
    database_path: str = "data/research.sqlite3"
    max_active_runs: int = Field(default=2, ge=1, le=8)
    max_document_chars: int = Field(default=18_000, ge=2_000, le=60_000)
    max_pdf_pages: int = Field(default=30, ge=1, le=100)
    max_fetch_bytes: int = Field(default=5_000_000, ge=100_000, le=20_000_000)
    model_max_output_tokens: int = Field(default=1200, ge=256, le=4000)


@lru_cache
def get_settings() -> Settings:
    return Settings()
