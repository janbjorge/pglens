"""Environment-driven settings, parsed and validated by pydantic-settings.

``Settings`` is instantiated exactly once, in ``app_lifespan``, and passed down
from there. Nothing else reads the environment.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    StringConstraints,
    computed_field,
    field_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_DB = "default"

StrippedStr = Annotated[str, StringConstraints(strip_whitespace=True)]


class DatabaseAliases(BaseModel):
    """Which databases to open a pool for, and which one tools target by default."""

    model_config = ConfigDict(frozen=True)

    names: list[str] = Field(description="Every configured alias, in declaration order.")
    default: str = Field(description="Alias used when a tool omits `database=`.")


class PoolOptions(BaseModel):
    """What the asyncpg pool needs to be safe against a production server."""

    model_config = ConfigDict(frozen=True)

    server_settings: dict[str, str] = Field(description="GUCs applied to every connection.")
    command_timeout: float | None = Field(
        description=(
            "Client-side deadline per command, a few seconds above statement_timeout so the "
            "server normally cancels first; it catches blackholed connections. None disables."
        )
    )


class Settings(BaseSettings):
    """All pglens configuration: the databases to open and the timeouts bounding
    what pglens can do to them. Timeouts are whole seconds and 0 disables, as in
    Postgres. Host and credentials come from the libpq env vars (PGHOST, PGUSER,
    PGPASSWORD, PGSSLMODE, ...), which asyncpg reads itself.
    """

    model_config = SettingsConfigDict(env_prefix="PGLENS_", extra="ignore")

    primary_database: StrippedStr = Field(
        default="",
        validation_alias="PGDATABASE",
        description="Primary dbname and default alias; empty leaves the dbname to libpq.",
    )
    # NoDecode hands the raw env string to the validator: the format is comma-separated, not JSON.
    extra_databases: Annotated[list[StrippedStr], NoDecode] = Field(
        default_factory=list,
        validation_alias="PGLENS_DATABASES",
        description="Further dbnames on the same host, comma-separated, each with its own pool.",
    )
    statement_timeout: NonNegativeInt = Field(
        default=60, description="Seconds any single query may run, EXPLAIN ANALYZE included."
    )
    lock_timeout: NonNegativeInt = Field(
        default=5,
        description="Seconds a statement waits for a lock; even a SELECT takes ACCESS SHARE.",
    )
    idle_tx_timeout: NonNegativeInt = Field(
        default=30,
        description=(
            "Seconds a session may idle inside a transaction; a stalled client would otherwise "
            "hold ACCESS SHARE on every table it touched and pin the xmin horizon."
        ),
    )
    max_cell_chars: NonNegativeInt = Field(
        default=1000,
        description=(
            "Longest text cell query returns; longer cells are cut and end with a marker "
            "giving the full length. 0 disables the cut."
        ),
    )

    @field_validator("extra_databases", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: str | list[str]) -> list[str]:
        return value.split(",") if isinstance(value, str) else value

    @computed_field
    @property
    def database_aliases(self) -> DatabaseAliases:
        """PGDATABASE first, then PGLENS_DATABASES deduplicated. Without either, one
        ``default`` alias leaves the dbname to libpq. Names are case-sensitive dbnames.
        """
        primary = self.primary_database or None
        extras: list[str] = []
        for name in self.extra_databases:
            if name and name not in extras and name != primary:
                extras.append(name)
        if primary is not None:
            return DatabaseAliases(names=[primary, *extras], default=primary)
        if extras:
            return DatabaseAliases(names=extras, default=extras[0])
        return DatabaseAliases(names=[DEFAULT_DB], default=DEFAULT_DB)

    @computed_field
    @property
    def pool_options(self) -> PoolOptions:
        return PoolOptions(
            server_settings={
                "application_name": "pglens",
                # Every connection read-only; _fetch_user adds an explicit transaction on top.
                "default_transaction_read_only": "on",
                "statement_timeout": str(self.statement_timeout * 1000),
                "lock_timeout": str(self.lock_timeout * 1000),
                "idle_in_transaction_session_timeout": str(self.idle_tx_timeout * 1000),
            },
            command_timeout=None if self.statement_timeout == 0 else self.statement_timeout + 5.0,
        )
