"""Environment configuration. Secrets are read only on the server."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UTL_", extra="ignore")

    database_url: str = "sqlite:///./data/urbantrafficlab.db"
    cors_origins: str = "http://localhost:3000"
    llm_provider: str = "none"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_timeout_s: float = 30.0
    llm_max_retries: int = 2
    sumo_binary: str = ""
    sumo_home: str = ""
    simulation_timeout_s: int = 120
    simulation_end_buffer_s: int = 900
    max_vehicles: int = 5000
    max_prompt_chars: int = 8000
    max_upload_bytes: int = 5_000_000
    max_features: int = 5000
    sync_jobs: bool = False
    osm_overpass_url: str = "https://overpass-api.de/api/interpreter"
    osm_timeout_s: float = 25.0
    osm_max_retries: int = 1
    osm_max_span_degrees: float = 0.2
    cache_dir: str = "./data/cache"
    map_tile_url: str = ""
    log_level: str = "INFO"

    @property
    def llm_configured(self) -> bool:
        return (
            self.llm_provider == "openai_compatible"
            and bool(self.llm_api_key and self.llm_base_url and self.llm_model)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
