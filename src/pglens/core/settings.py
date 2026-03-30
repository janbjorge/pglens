"""Application settings, loaded from PGLENS_* environment variables."""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PGLENS_")

    dsn: str | None = None
    schemas: frozenset[str] | None = None

    @field_validator("schemas", mode="before")
    @classmethod
    def parse_comma_separated(cls, v: object) -> object:
        if isinstance(v, str):
            parts = frozenset(s.strip() for s in v.split(",") if s.strip())
            return parts if parts else None
        return v
